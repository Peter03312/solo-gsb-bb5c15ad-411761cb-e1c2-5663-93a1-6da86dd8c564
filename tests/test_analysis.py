"""冲突约束分析单元测试：python tests/test_analysis.py

覆盖：
- 输入范围、等待边界、正权“先于”环的最小不可满足约束；
- 每条约束都是“缺一不可”，重复查询稳定；
- 暂存事件与被跳过关系不参与分析并给出原因；
- 带时区按 UTC 绝对分钟判定，响应保留当地时间和偏移。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

import analysis  # noqa: E402


def ev(key, name, es, ls, dur, timezone=None):
    item = {"key": key, "name": name, "earliest_start": es,
            "latest_start": ls, "duration_minutes": dur}
    if timezone:
        item["timezone"] = timezone
    return item


def rel(a, b, min_g=None, max_g=None):
    item = {"predecessor_key": a, "successor_key": b}
    if min_g is not None:
        item["min_gap_minutes"] = min_g
    if max_g is not None:
        item["max_gap_minutes"] = max_g
    return item


def assert_minimal(snapshot):
    result = analysis.analyze_snapshot(snapshot)
    assert result["status"] == "conflict"
    group = result["groups"][0]
    assert group["minimal_unsatisfiable"] is True
    assert group["satisfiable_after_removing_any_constraint"] is True

    # 直接从差分约束模型验证：删除任一条后均无正权环。
    by_id = {c["id"]: c for c in group["constraints"]}
    edges = []
    for c in by_id.values():
        edges.append({"cid": c["id"], "from": c["edge"]["from"],
                      "to": c["edge"]["to"], "weight": c["edge"]["weight_minutes"]})
    for cid in group["constraint_ids"]:
        rest = [edge for edge in edges if edge["cid"] != cid]
        assert analysis._positive_cycle(rest) is None, cid
    return result, by_id


def test_invalid_range_is_two_boundary_mus():
    snapshot = {"events": [ev("a", "A", "2026-10-01T11:00", "2026-10-01T10:00", 5)],
                "relations": []}
    result, by_id = assert_minimal(snapshot)
    group = result["groups"][0]
    assert group["type"] == "invalid_range"
    assert set(group["constraint_ids"]) == {
        "e0001:a:earliest_start", "e0001:a:latest_start"}
    assert all(c["time_basis"] == "naive_local" for c in group["constraints"])
    assert result == analysis.analyze_snapshot(snapshot)


def test_wait_boundary_mus_includes_predecessor_duration():
    snapshot = {"events": [
        ev("a", "A", "2026-10-01T10:00", "2026-10-01T11:00", 30),
        ev("b", "B", "2026-10-01T12:00", "2026-10-01T13:00", 0)],
        "relations": [rel("a", "b", min_g=0, max_g=10)]}
    result, by_id = assert_minimal(snapshot)
    assert result["groups"][0]["type"] == "window"
    max_constraint = by_id["r0001:max_wait"]
    assert max_constraint["predecessor_duration_minutes"] == 30
    assert max_constraint["difference_minutes"] == 40
    assert max_constraint["edge"] == {"from": "b", "to": "a", "weight_minutes": -40}
    assert set(result["groups"][0]["constraint_ids"]) == {
        "e0001:a:latest_start", "e0002:b:earliest_start", "r0001:max_wait"}


def test_positive_precedence_cycle_mus():
    snapshot = {"events": [
        ev("a", "A", "2026-10-01T10:00", "2026-10-01T12:00", 10),
        ev("b", "B", "2026-10-01T10:00", "2026-10-01T12:00", 20)],
        "relations": [rel("a", "b"), rel("b", "a")]}
    result, _ = assert_minimal(snapshot)
    group = result["groups"][0]
    assert group["type"] == "cycle"
    assert group["constraint_ids"] == ["r0001:min_wait", "r0002:min_wait"]
    assert group["related_event_ids"] == ["a", "b"]
    assert group["related_events"] == [
        {"id": "a", "uid": None, "name": "A", "timezone": None},
        {"id": "b", "uid": None, "name": "B", "timezone": None}]


def test_multipath_min_max_conflict_is_window_not_cycle():
    base = "2026-10-01T"
    snapshot = {"events": [
        ev("a", "A", base + "10:00", base + "10:00", 0),
        ev("b", "B", base + "10:00", base + "12:00", 0),
        ev("c", "C", base + "10:00", base + "12:00", 0),
        ev("d", "D", base + "10:00", base + "12:00", 0)],
        "relations": [rel("a", "b", min_g=60), rel("b", "d"),
                      rel("a", "c", max_g=10), rel("c", "d", max_g=10)]}
    result, _ = assert_minimal(snapshot)
    group = result["groups"][0]
    assert group["type"] == "window"
    assert group["constraint_ids"] == [
        "r0001:min_wait", "r0002:min_wait", "r0003:max_wait", "r0004:max_wait"]


def test_zero_duration_cycle_is_feasible_empty_group():
    snapshot = {"events": [
        ev("a", "A", "2026-10-01T10:00", "2026-10-01T12:00", 0),
        ev("b", "B", "2026-10-01T10:00", "2026-10-01T12:00", 0)],
        "relations": [rel("a", "b"), rel("b", "a")]}
    result = analysis.analyze_snapshot(snapshot)
    assert result["status"] == "ok" and result["groups"] == [] and result["group"] is None


def test_drafts_and_their_relations_excluded():
    snapshot = {"events": [
        ev("a", "A", "2026-10-01T11:00", "2026-10-01T10:00", 5),
        ev("d", "Draft", None, None, None)],
        "relations": [rel("a", "d", max_g=10)]}
    result = analysis.analyze_snapshot(snapshot)
    assert result["status"] == "conflict"
    assert result["groups"][0]["constraint_ids"] == [
        "e0001:a:earliest_start", "e0001:a:latest_start"]
    assert len(result["excluded_draft_events"]) == 1
    excluded = result["excluded_draft_events"][0]
    assert excluded["event_id"] == "d" and "暂存" in excluded["reason"]
    assert set(excluded["excluded_constraint_ids"]) == {
        "e0002:d:earliest_start", "e0002:d:latest_start"}
    skipped = result["excluded_skipped_relations"][0]
    assert skipped["relation_id"] == "r0001"
    assert "暂存状态" in skipped["reason"]
    assert skipped["excluded_constraint_ids"] == ["r0001:min_wait", "r0001:max_wait"]


def test_timezone_constraints_use_utc_absolute_minutes_but_preserve_local():
    snapshot = {"events": [
        ev("sh", "上海", "2026-10-01T10:00+08:00", "2026-10-01T10:00+08:00", 15,
           timezone="Asia/Shanghai"),
        ev("ny", "纽约", "2026-09-30T20:00-04:00", "2026-09-30T20:10-04:00", 30,
           timezone="America/New_York")],
        "relations": [rel("sh", "ny")]}
    result, by_id = assert_minimal(snapshot)
    assert result["groups"][0]["type"] == "window"
    earliest_sh = by_id["e0001:sh:earliest_start"]
    latest_ny = by_id["e0002:ny:latest_start"]
    # 上海 2026-10-01 10:00+08 = 02:00 UTC；纽约 20:10-04 = 00:10 UTC。
    assert earliest_sh["absolute_minutes"] - latest_ny["absolute_minutes"] == 110
    assert latest_ny["utc_offset_minutes"] == -240
    assert latest_ny["local_time"] == "2026-09-30T20:10-04:00"
    # A 固定 10:00+08 = 02:00 UTC，持续 15 分钟 ⇒ B 最早 02:15 UTC > 00:10 UTC。
    constraint = by_id["r0001:min_wait"]
    assert constraint["difference_minutes"] == 15
    assert constraint["time_basis"] == "utc_absolute"


def run():
    tests = [obj for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)


if __name__ == "__main__":
    run()
