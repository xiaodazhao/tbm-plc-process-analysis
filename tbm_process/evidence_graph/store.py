from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import VERSION


NODE_TYPES = {"ConstructionSegment", "GeologyDocument", "GeologyFact", "PLCResponse",
              "EngineeringComparison", "ReportClaim"}
RELATIONS = {
    "HAS_FACT": ({"GeologyDocument"}, {"GeologyFact"}),
    "COVERS": ({"GeologyFact"}, {"ConstructionSegment"}),
    "OBSERVED_AT": ({"GeologyFact"}, {"ConstructionSegment"}),
    "MEASURED_OVER": ({"PLCResponse"}, {"ConstructionSegment"}),
    "OVERLAPS_WITH": ({"ConstructionSegment"}, {"ConstructionSegment"}),
    "COMPARES": ({"EngineeringComparison"}, {"GeologyFact", "PLCResponse"}),
    "DIFFERS_FROM": ({"GeologyFact"}, {"GeologyFact"}),
    "DERIVED_FROM": ({"EngineeringComparison", "PLCResponse", "ReportClaim"},
                     {"GeologyFact", "PLCResponse", "EngineeringComparison"}),
    "SUPPORTED_BY": ({"ReportClaim"}, {"GeologyFact", "PLCResponse", "EngineeringComparison"}),
}


def identity(kind: str, *parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return kind + ":" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def intersects(a: dict, b: dict) -> bool:
    if a.get("chainage_system") != b.get("chainage_system") or a.get("line") != b.get("line"):
        return False
    if not all(a.get(k) is not None and b.get(k) is not None for k in ("start_m", "end_m")):
        return False
    if a["start_m"] == a["end_m"]:
        return b["start_m"] <= a["start_m"] <= b["end_m"]
    if b["start_m"] == b["end_m"]:
        return a["start_m"] <= b["start_m"] <= a["end_m"]
    return max(a["start_m"], b["start_m"]) < min(a["end_m"], b["end_m"])


class EvidenceGraph:
    """In-memory indexed graph with deterministic JSON and SQLite snapshots."""

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.outgoing: dict[str, list[str]] = {}
        self.incoming: dict[str, list[str]] = {}
        self.metadata = {"version": VERSION, "causal_edges_permitted": False}

    def add_node(self, node_type: str, node_id: str, data: dict) -> str:
        if node_type not in NODE_TYPES:
            raise ValueError("Unknown node type: " + node_type)
        node = {"id": node_id, "type": node_type, "data": data}
        if node_id in self.nodes and self.nodes[node_id] != node:
            raise ValueError("Conflicting duplicate node: " + node_id)
        self.nodes[node_id] = node
        return node_id

    def add_edge(self, relation: str, source: str, target: str, evidence: dict) -> str:
        if relation not in RELATIONS:
            raise ValueError("Unknown or forbidden relation: " + relation)
        if source not in self.nodes or target not in self.nodes:
            raise ValueError("Dangling edge")
        left, right = RELATIONS[relation]
        if self.nodes[source]["type"] not in left or self.nodes[target]["type"] not in right:
            raise ValueError("Relation endpoint type mismatch: " + relation)
        if source == target:
            raise ValueError("Self relation not supported")
        if not evidence.get("method"):
            raise ValueError("An edge requires an explicit construction method")
        key = identity("EDGE", relation, source, target)
        edge = {"id": key, "type": relation, "source": source, "target": target, "evidence": evidence}
        if key in self.edges:
            if self.edges[key] != edge:
                raise ValueError("Conflicting duplicate edge")
            return key
        self.edges[key] = edge
        self.outgoing.setdefault(source, []).append(key)
        self.incoming.setdefault(target, []).append(key)
        return key

    def neighbors(self, node_id: str, relation: str | None = None, incoming=False):
        index = self.incoming if incoming else self.outgoing
        for edge_id in index.get(node_id, []):
            edge = self.edges[edge_id]
            if relation is None or edge["type"] == relation:
                yield edge["source" if incoming else "target"]

    def typed(self, node_type: str):
        return (n for n in list(self.nodes.values()) if n["type"] == node_type)

    def trace(self, node_id: str) -> dict:
        if node_id not in self.nodes:
            raise KeyError(node_id)
        queue, seen, edges = deque([node_id]), set(), {}
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            for edge_id in self.outgoing.get(current, []):
                edge = self.edges[edge_id]
                if edge["type"] in {"SUPPORTED_BY", "DERIVED_FROM", "COMPARES", "MEASURED_OVER", "COVERS", "OBSERVED_AT"}:
                    edges[edge_id] = edge
                    queue.append(edge["target"])
            if self.nodes[current]["type"] == "GeologyFact":
                for edge_id in self.incoming.get(current, []):
                    edge = self.edges[edge_id]
                    if edge["type"] == "HAS_FACT":
                        edges[edge_id] = edge
                        queue.append(edge["source"])
        return {"root_id": node_id, "nodes": [self.nodes[x] for x in sorted(seen)],
                "edges": [edges[x] for x in sorted(edges)],
                "interpretation": "source_and_calculation_paths_not_proof_of_semantic_truth"}

    def snapshot(self) -> dict:
        return {"metadata": self.metadata, "nodes": [self.nodes[k] for k in sorted(self.nodes)],
                "edges": [self.edges[k] for k in sorted(self.edges)]}

    @classmethod
    def from_snapshot(cls, snapshot: dict):
        graph = cls()
        graph.metadata = snapshot["metadata"]
        for node in snapshot["nodes"]:
            graph.add_node(node["type"], node["id"], node["data"])
        for edge in snapshot["edges"]:
            graph.add_edge(edge["type"], edge["source"], edge["target"], edge["evidence"])
        return graph

    def save_sqlite(self, path: Path):
        if path.exists():
            raise FileExistsError(path)
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE nodes (id TEXT PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX node_types ON nodes(type);
                CREATE TABLE edges (id TEXT PRIMARY KEY, type TEXT NOT NULL,
                    source TEXT NOT NULL REFERENCES nodes(id), target TEXT NOT NULL REFERENCES nodes(id),
                    evidence TEXT NOT NULL, UNIQUE(type,source,target));
                CREATE INDEX edge_sources ON edges(source,type);
                CREATE INDEX edge_targets ON edges(target,type);
            """)
            conn.executemany("INSERT INTO metadata VALUES (?,?)",
                             [(k, json.dumps(v, ensure_ascii=False)) for k, v in self.metadata.items()])
            conn.executemany("INSERT INTO nodes VALUES (?,?,?)",
                             [(n["id"], n["type"], json.dumps(n["data"], ensure_ascii=False, allow_nan=False))
                              for n in self.nodes.values()])
            conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?)",
                             [(e["id"], e["type"], e["source"], e["target"], json.dumps(e["evidence"], ensure_ascii=False))
                              for e in self.edges.values()])

    @classmethod
    def load_sqlite(cls, path: Path):
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as conn:
            return cls.from_snapshot({
                "metadata": {k: json.loads(v) for k, v in conn.execute("SELECT key,value FROM metadata")},
                "nodes": [{"id": i, "type": t, "data": json.loads(v)} for i, t, v in conn.execute("SELECT id,type,payload FROM nodes")],
                "edges": [{"id": i, "type": t, "source": s, "target": d, "evidence": json.loads(v)}
                          for i, t, s, d, v in conn.execute("SELECT id,type,source,target,evidence FROM edges")]})
