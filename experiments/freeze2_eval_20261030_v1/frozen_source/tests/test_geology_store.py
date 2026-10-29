"""Small synthetic fixtures test query logic; no fixture is an engineering result."""
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from tbm_process.geology_store.database import GeologyStore, SCHEMA
from tbm_process.geology_store.server import handler_for
from tbm_process.geology_store.spatial import chainage, coverage_partition, union
from tbm_process.geology_store.time_policy import time_decision
from tbm_process.reporting_v2.geology import attributes


def document(key="hsp", kind="HSP", submission="2024-03-15"):
    return {"document_id": key, "document_type": kind, "source_file": "synthetic-test-only.json",
            "start_m": 0., "end_m": 25., "local_images": [], "date_audit": {"status": "PASS", "basis_field": "submission_date"},
            "metadata": {"submission_date": submission, "submission_date_source": {"source_page": 7},
                         "observation_date": None, "date_conflicts": []}}


@pytest.fixture
def database(tmp_path):
    path = tmp_path/"test.sqlite"
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    docs = [document("tsp", "TSP", "2024-03-13"), document(), document("future", "HSP", "2024-03-18"), document("sketch", "SKETCH")]
    docs[-1]["metadata"].update(available_date_user_confirmed=True, available_date="2024-03-16")
    specs = [("t", "tsp", 0, 20, "interval", "geological_description", "forecast", "板岩"),
             ("tg", "tsp", 0, 20, "interval", "rock_grade", "forecast", "IV"),
             ("h", "hsp", 10, 25, "interval", "geological_description", "forecast", "板岩"),
             ("hg", "hsp", 10, 25, "interval", "rock_grade", "forecast", "V"),
             ("f", "future", 24, 50, "interval", "geological_description", "forecast", "砂岩"),
             ("p", "sketch", 18.5, 18.5, "point", "geological_description", "observation", "板岩"),
             ("s", "sketch", 18.5, 18.5, "point", "rock_grade", "observation", "IV"),
             ("unlocated", "sketch", None, None, "unresolved", "geological_description", "observation", "局部出水"),
             ("risk", "hsp", 20, 20, "point", "risk", "forecast", "掉块")]
    for d in docs:
        c.execute("INSERT INTO documents VALUES ("+",".join("?"*17)+")", (d["document_id"], d["document_type"], "right", "DyK", 0, 25, None, None,
                  d["metadata"].get("submission_date"), None, d["metadata"].get("available_date"), None, "day", "test", "test.json", json.dumps(d), "{}"))
    for key, did, lo, hi, geom, kind, role, value in specs:
        record = did+":record"
        f = {"fact_id": key, "document_id": did, "record_id": record, "fact_type": kind, "information_role": role,
             "geometry": geom, "start_m": lo, "end_m": hi, "point_m": lo if geom == "point" else None,
             "document_type": next(d["document_type"] for d in docs if d["document_id"] == did),
             "attributes": attributes(value, value if kind == "rock_grade" else None), "value": value,
             "original_description": value, "original_text": value, "source": {"page": 10, "path": "test.json"},
             "extraction_quality": {"status": "PASS"}}
        c.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", (key, did, "test.json", "test", 10, None, value, "{}"))
        c.execute("INSERT INTO facts VALUES ("+",".join("?"*15)+")", (key, did, key, record, kind, role, geom, "right", "DyK", lo, hi, f["point_m"], None, "PASS", json.dumps(f)))
    c.commit(); c.close()
    return path


@pytest.mark.parametrize("value,expected", [("DyK1015+392", 1015392), ("dyk1015+392.125", 1015392.125), (1015392.125, 1015392.125), ("1015392.125", 1015392.125)])
def test_chainage_exact(value, expected):
    assert chainage(value) == expected


@pytest.mark.parametrize("value", ["DK1015+392", "DyK1015+1001", "1015+392", float("nan"), float("inf")])
def test_chainage_not_guessed(value):
    with pytest.raises(ValueError):
        chainage(value)


@pytest.mark.parametrize("as_of,eligible", [("2024-03-14", False), ("2024-03-15", False), ("2024-03-15T23:59:59", False), ("2024-03-16", True), (None, True)])
def test_submission_policy(as_of, eligible):
    assert time_decision(document(), as_of)["eligible"] is eligible


def test_test_date_or_cover_is_not_submission():
    d = document(submission=None)
    d["metadata"].update(test_date="2024-03-01", cover_date="2024-03-02")
    assert not time_decision(d, "2024-04-01")["eligible"]


def test_unreliable_date_is_review():
    d = document()
    d["metadata"]["date_conflicts"] = ["disagreement"]
    assert time_decision(d, "2024-04-01")["status"] == "REVIEW"


def test_available_date_not_observation_date():
    d = document(kind="SKETCH")
    d["metadata"].update(available_date="2024-03-16", available_date_user_confirmed=True)
    assert time_decision(d, "2024-03-17")["eligible"]
    assert not d["metadata"]["observation_date"]


def test_verified_timestamp_exclusive_boundary():
    d = document()
    d["metadata"].update(available_at="2024-03-15T08:00:00+08:00", actual_distribution_time_verified=True)
    assert not time_decision(d, "2024-03-15T08:00:00+08:00")["eligible"]
    assert time_decision(d, "2024-03-15T08:00:01+08:00")["eligible"]
    d["metadata"]["actual_distribution_time_verified"] = False
    assert time_decision(d, "2024-04-01")["status"] == "REVIEW"


def test_union_does_not_double_count():
    assert union([[0, 20], [10, 25], [25, 28], [30, 30]]) == [[0., 28.]]


def test_original_partition_boundaries(database):
    store = GeologyStore(database)
    c = store.query_geological_coverage(0, 30, as_of="2024-03-16")
    assert [(r["start_m"], r["end_m"], r["category"]) for r in c["partitions"]] == [(0, 10, "tsp_only"), (10, 20, "both"), (20, 25, "hsp_only"), (25, 30, "gap")]
    assert (c["tsp_m"], c["hsp_m"], c["union_m"], c["overlap_m"], c["gap_m"]) == (20, 15, 25, 10, 5)
    store.close()


def test_point_does_not_cover_interval(database):
    store = GeologyStore(database)
    q = store.query_geology_by_point(18.5, source_types=["SKETCH"])
    assert {f["fact_id"] for f in q["facts"]} == {"p", "s"}
    assert not store.query_geology_by_chainage(19, 20, source_types=["SKETCH"])["facts"]
    assert store.query_geological_coverage(25, 30, as_of="2024-03-16")["union_m"] == 0
    store.close()


def test_no_cross_line_or_system(database):
    store = GeologyStore(database)
    assert not store.query_geology_by_chainage(0, 50, system="DK")["facts"]
    assert not store.query_geology_by_chainage(0, 50, line="left")["facts"]
    store.close()


@pytest.mark.parametrize("distance", [10, 13, 20, 50])
def test_custom_ahead_distances(database, distance):
    store = GeologyStore(database)
    q = store.query_forecasts_ahead("2024-03-16", 20, distance, 1)
    assert q["query"]["range_m"] == [20, 20+distance]
    assert q["coverage"]["union_m"] == 5
    assert all(f["document_type"] in {"TSP", "HSP"} for f in q["query"]["facts"])
    assert not any(f["fact_id"] == "risk" for f in q["query"]["facts"])
    store.close()


def test_reverse_ahead(database):
    store = GeologyStore(database)
    q = store.query_forecasts_ahead("2024-03-16", 24, 13, -1)
    assert q["query"]["range_m"] == [11, 24]
    risk = next(f for f in q["query"]["facts"] if f["fact_id"] == "risk")
    assert risk["distances_from_current_m"] == [4, 4]
    store.close()


@pytest.mark.parametrize("distance,direction", [(0, 1), (-1, 1), (float("nan"), 1), (13, None), (13, 0)])
def test_invalid_ahead_rejected(database, distance, direction):
    store = GeologyStore(database)
    with pytest.raises(ValueError):
        store.query_forecasts_ahead("2024-03-16", 20, distance, direction)
    store.close()


def test_future_excluded_without_removing_all_history(database):
    store = GeologyStore(database)
    all_history = store.query_geological_coverage(0, 50)
    before = store.query_geological_coverage(0, 50, as_of="2024-03-16")
    assert all_history["union_m"] == 50 and before["union_m"] == 25
    assert "future" in before["excluded_documents"]
    store.close()


def test_grade_comparison_preserves_both_sources(database):
    store = GeologyStore(database)
    q = store.query_previous_forecasts_for_excavation("2024-03-16", 0, 25)
    comparisons = store.forecast_comparisons(q)
    assert len(comparisons) == 1
    assert comparisons[0]["range_m"] == [10, 20]
    assert comparisons[0]["status"] == "explicit_reported_grade_difference"
    assert comparisons[0]["attributes"]["grade"]["left"] == ["IV"]
    store.close()


def test_previous_forecast_string_bounds_use_numeric_order(database):
    store = GeologyStore(database)
    q = store.query_previous_forecasts_for_excavation("2024-03-16", "DyK0+9", "DyK0+20")
    assert q["range_m"] == [9, 20]
    assert {d["document_id"] for d in q["documents"]} == {"tsp", "hsp"}
    store.close()


def test_sketch_no_far_historical_fallback(database):
    store = GeologyStore(database)
    assert not store.daily_sketches("2024-03-17")
    assert len(store.daily_sketches("2024-03-16")) == 1
    result = store.compare_forecast_with_sketch("sketch")
    assert result["observation_date"] is None
    assert result["report_available_date"] == "2024-03-16"
    assert result["unlocated_observation_fact_ids"] == ["unlocated"]
    assert result["comparisons"]
    assert all(not c["observation_date_confirmed"] for c in result["comparisons"])
    store.close()


def test_database_is_read_only(database):
    store = GeologyStore(database)
    with pytest.raises(sqlite3.OperationalError):
        store.con.execute("DELETE FROM facts")
    store.close()


def test_local_http_queries_and_source_path_security(database):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(database))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(address+"/api/query?start=0&end=30&as_of=2024-03-16") as stream:
            result = json.load(stream)
        assert result["coverage"]["union_m"] == 25
        with urlopen(address+"/api/fact?id=t&as_of=2024-03-16") as stream:
            detail = json.load(stream)
        assert not any(f["document_id"] == "future" for f in detail["peer_forecasts"])
        with pytest.raises(HTTPError):
            urlopen(address+"/api/source?id=../../etc/passwd")
        with pytest.raises(HTTPError):
            urlopen(address+"/api/ahead?current=20&distance=13&direction=1")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
