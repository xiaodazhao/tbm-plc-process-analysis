from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tbm_process.reporting_v2.facts import digest

from .database import GeologyStore
from .spatial import chainage
from .visualization import plotly_axis


def handler_for(database):
    class Handler(BaseHTTPRequestHandler):
        def send_data(self, body, kind="application/json; charset=utf-8", status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            arg = lambda k, default=None: params.get(k, [default])[0]
            try:
                if parsed.path == "/":
                    self.send_data(Path(__file__).with_name("ui.html").read_bytes(), "text/html; charset=utf-8")
                    return
                if parsed.path == "/plotly.js":
                    from plotly.offline import get_plotlyjs
                    self.send_data(get_plotlyjs().encode(), "application/javascript; charset=utf-8")
                    return
                store = GeologyStore(database)
                try:
                    if parsed.path == "/api/meta":
                        self.send_data(store.metadata)
                    elif parsed.path in {"/api/source", "/api/image"}:
                        doc = store.document(arg("id"))
                        # Only registered source documents can be read, never an arbitrary supplied path.
                        if parsed.path == "/api/image":
                            index = int(arg("index", "0"))
                            if index < 0 or index >= len(doc.get("local_images", [])):
                                raise ValueError("Unknown image")
                            ref = doc["local_images"][index]
                            path, expected_hash = Path(ref["path"]), ref["sha256"]
                        else:
                            path, expected_hash = Path(doc["original_file_path"]), doc["source_version"]
                        candidates = [path, Path(database).parent/"source_files"/expected_hash/path.name]
                        path = next((p for p in candidates if p.is_file() and digest(p) == expected_hash), None)
                        if path is None:
                            raise FileNotFoundError("Original source is not available on this computer")
                        self.send_data(path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    elif parsed.path == "/api/fact":
                        row = store.con.execute("SELECT data_json FROM facts WHERE fact_id=?", (arg("id"),)).fetchone()
                        if row is None:
                            raise KeyError("Unknown fact")
                        fact = json.loads(row[0])
                        peers = store.query_geology_by_chainage(fact["start_m"], fact["end_m"], as_of=arg("as_of") or None,
                                                                source_types=("TSP", "HSP"))["facts"]
                        self.send_data({"fact": fact, "document": store.document(fact["document_id"]),
                                        "unlocated_source_facts": [r for r in store.document_facts(fact["document_id"]) if r.get("start_m") is None],
                                        "peer_forecasts": [f for f in peers if f["fact_type"] == "geological_description" and f["document_id"] != fact["document_id"]]})
                    elif parsed.path in {"/api/query", "/api/ahead"}:
                        as_of = arg("as_of") or None
                        if parsed.path == "/api/ahead":
                            if not as_of:
                                raise ValueError("Historical ahead query requires a cutoff date")
                            result = store.query_forecasts_ahead(as_of, arg("current"), float(arg("distance")), int(arg("direction")))
                            query, cov = result["query"], result["coverage"]
                        else:
                            lo, hi = chainage(arg("start")), chainage(arg("end"))
                            query = store.query_geology_by_chainage(lo, hi, as_of=as_of, source_types=[arg("source_type")] if arg("source_type") else None)
                            cov = store.query_geological_coverage(lo, hi, as_of=as_of)
                        lo, hi = query["range_m"]
                        plc_day = arg("plc_day")
                        if plc_day:
                            try:
                                store.day(plc_day)
                            except ValueError:
                                plc_day = None
                        figure = plotly_axis(store, lo, hi, as_of=as_of, plc_day=plc_day,
                                             source_types=[arg("source_type")] if arg("source_type") and parsed.path != "/api/ahead" else None,
                                             ahead=[lo, hi] if parsed.path == "/api/ahead" else None)
                        self.send_data({"query": query, "coverage": cov, "figure": figure})
                    else:
                        self.send_data({"error": "Not found"}, status=404)
                finally:
                    store.close()
            except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
                self.send_data({"error": str(exc)}, status=400)

    return Handler


def serve(database, port=8766):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(database))
    print(f"URL=http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()
