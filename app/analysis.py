"""不可变版本快照的冲突约束分析。

目标：当某个可回看版本存在冲突时，找出一组**不可再缩减**的输入约束
（minimal unsat/MUS）：这组约束整体不可满足，而移去其中任意一条后，
剩余约束都可满足。

约束均以事件开始时间为变量，整理成最长路径差分约束：
  事件最早开始 E：              origin → event，权重 E
  事件最晚开始 L：              event → origin，权重 -L
  A 先于 B、A 持续 d、最短等待 g：B-A ≥ d+g，即 A → B，权重 d+g
  A 先于 B、A 持续 d、最长等待 m：B-A ≤ d+m，即 B → A，权重 -(d+m)
该差分系统不可满足当且仅当图中存在总权重为正的环。先用 Bellman-Ford
取出一个正环，再逐条试删，得到稳定且不可再缩减的约束集合。

带时区事件先换算成 UTC 绝对分钟；未带时区事件沿用朴素当地时间，内部使用
独立的分钟坐标，二者不会通过“先于”关系连通（保存时已禁止混合关系）。
暂存事件和被其跳过的关系与 compute 引擎使用同一套排除规则，不参与分析。
"""

import hashlib
from datetime import datetime, timezone

import compute

ORIGIN = "__time_origin__"
NAIVE_EPOCH = datetime(2000, 1, 1)
UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse(value):
    return datetime.fromisoformat(value)


def _offset_minutes(dt):
    if dt.tzinfo is None:
        return None
    return int(dt.utcoffset().total_seconds() // 60)


def _minute_value(dt):
    """带时区时间用 UTC 绝对分钟；朴素时间用独立的当地分钟坐标。"""
    if dt.tzinfo is not None:
        return int((dt.astimezone(timezone.utc) - UTC_EPOCH).total_seconds() // 60)
    return int((dt - NAIVE_EPOCH).total_seconds() // 60)


def _gap_min(relation):
    value = relation.get("min_gap_minutes")
    return 0 if value is None else int(value)


def _gap_max(relation):
    value = relation.get("max_gap_minutes")
    return None if value is None else int(value)


def _relation_id(ordinal):
    return "r{:04d}".format(ordinal)


def _live_input(snapshot_input):
    """把快照中的 key 引用转换成 compute 引擎需要的稳定 id。"""
    events = []
    for event in snapshot_input.get("events") or []:
        item = dict(event)
        item["id"] = event["key"]
        events.append(item)

    relations = []
    for ordinal, relation in enumerate(snapshot_input.get("relations") or [], start=1):
        rid = _relation_id(ordinal)
        relations.append({
            "id": rid,
            "predecessor_id": relation["predecessor_key"],
            "successor_id": relation["successor_key"],
            "min_gap_minutes": _gap_min(relation),
            "max_gap_minutes": _gap_max(relation),
        })
    return events, relations


def _event_cid(ordinal, key, bound):
    return "e{:04d}:{}:{}".format(ordinal, key, bound)


def _relation_cid(ordinal, bound):
    return "{}:{}".format(_relation_id(ordinal), bound)


def _time_basis(dt):
    return "utc_absolute" if dt.tzinfo is not None else "naive_local"


def _positive_cycle(edges):
    """返回正环上的约束 id；无正环（约束可满足）返回 None。

    所有节点距离初始化为 0，等价于为每个变量连接一个不参与环提取的虚拟源；
    边按 constraint_id 固定顺序松弛，因此同一快照的重复分析结果稳定。
    """
    nodes = {ORIGIN}
    edge_by_id = {}
    for edge in edges:
        nodes.add(edge["from"])
        nodes.add(edge["to"])
        edge_by_id[edge["cid"]] = edge

    ordered = sorted(edges, key=lambda edge: edge["cid"])
    dist = {node: 0 for node in nodes}
    pred = {node: None for node in nodes}
    last_updated = []

    for _ in range(len(nodes)):
        changed = False
        last_updated = []
        for edge in ordered:
            candidate = dist[edge["from"]] + edge["weight"]
            if candidate > dist[edge["to"]]:
                dist[edge["to"]] = candidate
                pred[edge["to"]] = edge
                changed = True
                last_updated.append(edge["to"])
        if not changed:
            return None

    # 第 V 轮仍可松弛：前驱图中必有正权环。从本轮更新的节点回溯定位。
    for start in last_updated or list(nodes):
        seen = {}
        node_path = []
        cur = start
        while cur is not None and cur not in seen:
            seen[cur] = len(node_path)
            node_path.append(cur)
            edge = pred[cur]
            cur = edge["from"] if edge else None
        if cur is None:
            continue
        cycle_nodes = node_path[seen[cur]:]
        cycle_ids = []
        for node in cycle_nodes:
            edge = pred[node]
            if edge and edge["cid"] not in cycle_ids:
                cycle_ids.append(edge["cid"])
        if sum(edge_by_id[cid]["weight"] for cid in cycle_ids) > 0:
            return cycle_ids
    return None


def _minimize(edges):
    """从一个正环出发，逐条试删，得到最小不可满足约束集合。"""
    seed = _positive_cycle(edges)
    if seed is None:
        return []
    edge_by_id = {edge["cid"]: edge for edge in edges}
    required = set(seed)
    # 初始正环已经很短；仍按固定顺序验证每一条，保证“缺一不可”。
    for cid in sorted(required):
        candidate = required - {cid}
        if _positive_cycle([edge_by_id[x] for x in candidate]) is not None:
            required = candidate
    return sorted(required)


def _boundary_constraints(events, draft_keys):
    edges = []
    constraints = {}
    for ordinal, event in enumerate(events, start=1):
        if event["key"] in draft_keys:
            continue
        key = event["key"]
        earliest = _parse(event["earliest_start"])
        latest = _parse(event["latest_start"])
        earliest_value = _minute_value(earliest)
        latest_value = _minute_value(latest)

        cid = _event_cid(ordinal, key, "earliest_start")
        edges.append({"cid": cid, "from": ORIGIN, "to": key,
                      "weight": earliest_value})
        constraints[cid] = {
            "id": cid,
            "kind": "event_boundary",
            "bound": "earliest_start",
            "event_ids": [key],
            "time_basis": _time_basis(earliest),
            "local_time": event["earliest_start"],
            "timezone": event.get("timezone"),
            "utc_offset_minutes": _offset_minutes(earliest),
            "absolute_minutes": earliest_value if earliest.tzinfo else None,
            "edge": {"from": ORIGIN, "to": key, "weight_minutes": earliest_value},
            "message": "事件「{}」的最早开始为 {}".format(
                event["name"], event["earliest_start"]),
        }

        cid = _event_cid(ordinal, key, "latest_start")
        edges.append({"cid": cid, "from": key, "to": ORIGIN,
                      "weight": -latest_value})
        constraints[cid] = {
            "id": cid,
            "kind": "event_boundary",
            "bound": "latest_start",
            "event_ids": [key],
            "time_basis": _time_basis(latest),
            "local_time": event["latest_start"],
            "timezone": event.get("timezone"),
            "utc_offset_minutes": _offset_minutes(latest),
            "absolute_minutes": latest_value if latest.tzinfo else None,
            "edge": {"from": key, "to": ORIGIN, "weight_minutes": -latest_value},
            "message": "事件「{}」的最晚开始为 {}".format(
                event["name"], event["latest_start"]),
        }
    return edges, constraints


def _relation_constraints(relations, events, active_keys):
    """为参与分析的快照关系建立约束；暂存事件或缺失端点的关系保持跳过。"""
    event_by_key = {event["key"]: event for event in events}
    edges = []
    constraints = {}
    for ordinal, relation in enumerate(relations, start=1):
        a_key = relation["predecessor_key"]
        b_key = relation["successor_key"]
        if a_key not in active_keys or b_key not in active_keys:
            continue
        a = event_by_key[a_key]
        b = event_by_key[b_key]
        duration = int(a["duration_minutes"])
        min_gap = _gap_min(relation)
        max_gap = _gap_max(relation)
        basis = _time_basis(_parse(a["earliest_start"]))
        rid = _relation_id(ordinal)
        relation_ref = {
            "id": rid,
            "ordinal": ordinal,
            "predecessor_event_id": a["key"],
            "successor_event_id": b["key"],
            "predecessor_name": a["name"],
            "successor_name": b["name"],
            "min_gap_minutes": min_gap,
            "max_gap_minutes": max_gap,
        }

        lower = duration + min_gap
        cid = _relation_cid(ordinal, "min_wait")
        edges.append({"cid": cid, "from": a["key"], "to": b["key"], "weight": lower})
        gap_text = "、最短等待 {} 分钟".format(min_gap) if min_gap else ""
        constraints[cid] = {
            "id": cid,
            "kind": "precedence",
            "bound": "min_wait",
            "event_ids": [a["key"], b["key"]],
            "relation_id": rid,
            "relation": relation_ref,
            "time_basis": basis,
            "predecessor_duration_minutes": duration,
            "min_gap_minutes": min_gap,
            "difference_minutes": lower,
            "edge": {"from": a["key"], "to": b["key"], "weight_minutes": lower},
            "message": "「{}」先于「{}」：前项持续 {} 分钟{}，后项开始至少晚 {} 分钟".format(
                a["name"], b["name"], duration, gap_text, lower),
        }

        if max_gap is not None:
            upper = duration + max_gap
            cid = _relation_cid(ordinal, "max_wait")
            edges.append({"cid": cid, "from": b["key"], "to": a["key"], "weight": -upper})
            constraints[cid] = {
                "id": cid,
                "kind": "precedence",
                "bound": "max_wait",
                "event_ids": [a["key"], b["key"]],
                "relation_id": rid,
                "relation": relation_ref,
                "time_basis": basis,
                "predecessor_duration_minutes": duration,
                "max_gap_minutes": max_gap,
                "difference_minutes": upper,
                "edge": {"from": b["key"], "to": a["key"], "weight_minutes": -upper},
                "message": "「{}」先于「{}」：前项持续 {} 分钟、最长等待 {} 分钟，"
                           "后项开始至多晚 {} 分钟".format(
                               a["name"], b["name"], duration, max_gap, upper),
            }
    return edges, constraints


def _group_type(cids, constraint_by_id):
    types = [constraint_by_id[cid]["kind"] for cid in cids]
    bounds = [constraint_by_id[cid].get("bound") for cid in cids]
    if not any(kind == "precedence" for kind in types):
        return "invalid_range"
    if all(kind == "precedence" for kind in types) and bounds == ["min_wait"] * len(bounds):
        return "cycle"
    return "window"


def _group_message(group_type):
    if group_type == "invalid_range":
        return "输入的最早、最晚开始边界自相矛盾，该组约束不可满足"
    if group_type == "cycle":
        return "“先于”关系的最短等待（含前项持续时间）构成正权环，关系不可满足"
    return "最早开始边界、最晚开始边界与等待约束共同构成不可满足的时间窗闭环"


def _excluded_events(events, computed_result):
    draft_by_key = {item["id"]: item for item in computed_result.get("drafts", [])}
    result = []
    for ordinal, event in enumerate(events, start=1):
        item = draft_by_key.get(event["key"])
        if item is None:
            continue
        result.append({
            "event_id": event["key"],
            "uid": event.get("uid"),
            "name": event["name"],
            "timezone": event.get("timezone"),
            "reason": item["reason"],
            "excluded_constraint_ids": [
                _event_cid(ordinal, event["key"], "earliest_start"),
                _event_cid(ordinal, event["key"], "latest_start"),
            ],
        })
    return result


def _excluded_relations(relations, computed_result):
    skipped_by_id = {item["id"]: item for item in computed_result.get("skipped_relations", [])}
    result = []
    for ordinal, relation in enumerate(relations, start=1):
        rid = _relation_id(ordinal)
        item = skipped_by_id.get(rid)
        if item is None:
            continue
        excluded = [_relation_cid(ordinal, "min_wait")]
        if _gap_max(relation) is not None:
            excluded.append(_relation_cid(ordinal, "max_wait"))
        result.append({
            "relation_id": rid,
            "predecessor_event_id": item["predecessor_id"],
            "successor_event_id": item["successor_id"],
            "min_gap_minutes": item["min_gap_minutes"],
            "max_gap_minutes": item["max_gap_minutes"],
            "reason": item["reason"],
            "excluded_constraint_ids": excluded,
        })
    return result


def analyze_snapshot(snapshot_input):
    """分析快照输入，返回稳定的只读冲突约束分析结果。"""
    raw_events = snapshot_input.get("events") or []
    raw_relations = snapshot_input.get("relations") or []
    events, live_relations = _live_input(snapshot_input)
    computed_result = compute.compute(events, live_relations)

    draft_keys = {item["id"] for item in computed_result["drafts"]}
    skipped_ids = {item["id"] for item in computed_result["skipped_relations"]}

    edges, constraint_by_id = _boundary_constraints(events, draft_keys)
    active_keys = {event["id"] for event in events if event["id"] not in draft_keys}
    relation_edges, relation_constraints = _relation_constraints(
        raw_relations, events, active_keys)
    edges.extend(relation_edges)
    constraint_by_id.update(relation_constraints)

    groups = []
    minimal_ids = _minimize(edges)
    if minimal_ids:
        group_type = _group_type(minimal_ids, constraint_by_id)
        digest = hashlib.sha1("\n".join(minimal_ids).encode("utf-8")).hexdigest()[:12]
        related_event_ids = []
        for cid in minimal_ids:
            for event_id in constraint_by_id[cid]["event_ids"]:
                if event_id not in related_event_ids:
                    related_event_ids.append(event_id)
        event_payload_by_id = {event["key"]: event for event in events}
        related_events = []
        for event_id in related_event_ids:
            event = event_payload_by_id[event_id]
            related_events.append({
                "id": event["key"],
                "uid": event.get("uid"),
                "name": event["name"],
                "timezone": event.get("timezone"),
            })
        groups.append({
            "id": "mus-" + digest,
            "type": group_type,
            "message": _group_message(group_type),
            "constraint_ids": minimal_ids,
            "constraints": [constraint_by_id[cid] for cid in minimal_ids],
            "related_event_ids": related_event_ids,
            "related_events": related_events,
            "minimal_unsatisfiable": True,
            "satisfiable_after_removing_any_constraint": True,
        })

    event_payload = [{
        "id": event["key"],
        "uid": event.get("uid"),
        "name": event["name"],
        "timezone": event.get("timezone"),
        "earliest_start": event.get("earliest_start"),
        "latest_start": event.get("latest_start"),
        "duration_minutes": event.get("duration_minutes"),
        "draft": event["key"] in draft_keys,
    } for event in events]

    relation_payload = []
    event_by_key = {event["key"]: event for event in events}
    for ordinal, relation in enumerate(raw_relations, start=1):
        rid = _relation_id(ordinal)
        a = event_by_key.get(relation["predecessor_key"])
        b = event_by_key.get(relation["successor_key"])
        relation_payload.append({
            "id": rid,
            "predecessor_event_id": relation["predecessor_key"],
            "successor_event_id": relation["successor_key"],
            "predecessor_name": a["name"] if a else None,
            "successor_name": b["name"] if b else None,
            "min_gap_minutes": _gap_min(relation),
            "max_gap_minutes": _gap_max(relation),
            "excluded": rid in skipped_ids,
            "exclusion_reason": next(
                (item["reason"] for item in computed_result["skipped_relations"]
                 if item["id"] == rid), None),
        })

    return {
        "status": "conflict" if groups else "ok",
        "groups": groups,
        "group": groups[0] if groups else None,
        "events": event_payload,
        "relations": relation_payload,
        "excluded_draft_events": _excluded_events(raw_events, computed_result),
        "excluded_skipped_relations": _excluded_relations(raw_relations, computed_result),
        "exclusion_reason": "暂存事件缺少完整时间范围或持续时间；其关联关系随暂存事件一起跳过，"
                           "不进入差分约束图。引用不存在事件的关系同样跳过。",
    }
