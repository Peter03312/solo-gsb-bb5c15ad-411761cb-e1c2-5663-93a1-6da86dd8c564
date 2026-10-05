"""冲突约束分析 HTTP API 端到端测试：python tests/test_analysis_api.py"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name
os.environ["PORT"] = "0"

import db  # noqa: E402
import server  # noqa: E402

db.init_db(server.DB_PATH)
HTTPD = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = HTTPD.server_address[1]
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def req(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(
        "http://127.0.0.1:{}{}".format(PORT, path), data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def ev(key, name, es, ls, dur):
    return {"key": key, "name": name, "earliest_start": es, "latest_start": ls,
            "duration_minutes": dur}


def setup_conflict():
    status, bundle = req("POST", "/api/reviews", {"title": "冲突分析"})
    assert status == 201
    rid = bundle["review"]["id"]
    # A 最晚 11:00 + 持续30 + 最长10 = B 不得晚于 11:40；B 最早 12:00。
    payload = {
        "base_version": 1, "title": "冲突分析",
        "events": [ev("a", "A", "2026-10-01T10:00", "2026-10-01T11:00", 30),
                   ev("b", "B", "2026-10-01T12:00", "2026-10-01T13:00", 0)],
        "relations": [{"predecessor_key": "a", "successor_key": "b",
                       "min_gap_minutes": 0, "max_gap_minutes": 10}]}
    status, bundle = req("PUT", "/api/reviews/{}".format(rid), payload)
    assert status == 200 and bundle["computation"]["status"] == "conflict"
    return rid


def test_conflict_analysis_returns_stable_minimal_group():
    rid = setup_conflict()
    status, first = req("GET", "/api/reviews/{}/versions/2/analysis".format(rid))
    assert status == 200, first
    assert first["status"] == "conflict" and first["version"] == 2
    group = first["group"]
    assert group["minimal_unsatisfiable"] is True
    assert group["satisfiable_after_removing_any_constraint"] is True
    assert "r0001:max_wait" in group["constraint_ids"]
    assert len(group["constraint_ids"]) == 3
    assert [c["kind"] for c in group["constraints"]].count("event_boundary") == 2
    assert group["constraints"][-1]["predecessor_duration_minutes"] == 30
    status, second = req("GET", "/api/reviews/{}/versions/2/analysis".format(rid))
    assert status == 200 and second == first


def test_no_conflict_returns_empty_group_and_analysis_readonly():
    rid = setup_conflict()
    before = req("GET", "/api/reviews/{}".format(rid))[1]
    status, data = req("GET", "/api/reviews/{}/versions/1/analysis".format(rid))
    assert status == 200
    assert data["groups"] == [] and data["group"] is None and data["status"] == "ok"
    assert data["analysis_readonly"] is True
    after = req("GET", "/api/reviews/{}".format(rid))[1]
    assert after["review"]["version"] == before["review"]["version"]
    assert after["review"]["status"] == before["review"]["status"]
    assert after["computation"]["result"] == before["computation"]["result"]


def test_analysis_missing_and_legacy_versions():
    rid = setup_conflict()
    status, data = req("GET", "/api/reviews/{}/versions/99/analysis".format(rid))
    assert status == 404 and data["error"] == "该版本不存在"

    status, missing_review = req("GET", "/api/reviews/999999/versions/1/analysis")
    assert status == 404 and missing_review["error"] == "复盘不存在"

    # 模拟旧库：v1 有 computation 但无 snapshot，分析必须明确拒绝。
    conn = sqlite3.connect(server.DB_PATH)
    conn.execute("DELETE FROM snapshots WHERE review_id=? AND review_version=1", (rid,))
    conn.commit()
    conn.close()
    status, legacy = req("GET", "/api/reviews/{}/versions/1/analysis".format(rid))
    assert status == 422 and legacy["analyzable"] is False
    assert "缺少完整输入快照" in legacy["error"]


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)


if __name__ == "__main__":
    run()
