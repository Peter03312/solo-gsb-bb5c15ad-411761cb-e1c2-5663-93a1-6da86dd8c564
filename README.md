# 事故复盘时间线（Retro Timeline）

供小组在本地协作的事故复盘时间线工具：记录事件的起始时间范围、持续分钟数与“先于”关系，
自动推算每个事件的可行时间窗并给出推算依据链。Python 标准库服务端 + SQLite 持久化 +
浏览器单页界面，无任何第三方依赖。

## 功能

- **事件建模**：每个事件包含起始时间范围（最早/最晚开始）、持续分钟数与备注。
- **“先于”约束（含间隔设置）**：A 先于 B 表示 A 结束后 B 才能开始。每条关系可设置
  **最短等待**（非负整数分钟，缺省 0）与**最长等待**（非负整数分钟，可空 = 不限），
  即 `A 结束 + 最短等待 ≤ B 开始 ≤ A 结束 + 最长等待`。未设置间隔的旧关系仍按旧语义
  `A 开始 + A 持续 ≤ B 开始` 推算，结果与升级前一致。最长等待不得短于最短等待，
  非法输入（负数、非整数、最长 < 最短）会被拒绝（HTTP 400）且不改任何记录。
- **时间窗推算**：前向/后向约束传播，同时施加每条关系的间隔上下界，给出每个事件的
  最早/最晚可行开始（即可行时间窗），并展示从输入到结论的完整依据链（含间隔取值）。
- **输入与结果分离保存**：原始输入存于 `events` / `relations` 表，每次保存的推算结果
  单独写入 `computations` 表（按复盘版本留存），互不覆盖。
- **版本回看与恢复**：每次保存/定稿成功时，除推算结果外另写一条**不可变输入快照**
  （标题、事件、“先于”关系及各自的间隔设置）到 `snapshots` 表，与当次推算一一对应。
  网页和 HTTP API 可列出各版本、回看当时输入与推算依据；把指定版本恢复时会
  **另存为当前复盘的新草稿版本**并重新推算，不覆盖任何旧版本。恢复须携带当前
  `base_version`，版本过期沿用 409 冲突语义。冲突或暂存输入允许恢复，但仍不得定稿。
- **版本对照**：任选同一复盘中较早、较晚两个版本对照（只读，不改当前输入、版本号
  与定稿状态），查看事件新增/删除/改名、输入时间范围与推算可行窗口的变化，以及
  “先于”关系间隔、冲突与暂存状态的变化。跨版本按**事件持续身份**（`uid`，保存时由
  服务端下发、客户端随下次保存回传，恢复时沿用快照身份）配对：改名或恢复后仍识别
  为同一事件，绝不按名称或会重建的行号猜配。旧库缺快照的版本明确报“无法比较”；
  有快照但缺可靠身份（升级前保存）的版本只展示各版本原貌，不虚构逐事件差值。
- **旧库历史版本**：升级前只有推算结果、缺少完整输入快照的历史版本，会在版本列表中
  明确标记为「不可回看、不可恢复」，不会用当前输入冒充；老接口行为保持不变。
- **冲突处理**：约束冲突时保留全部输入，指出冲突事件及双方依据链（正推最早可行开始
  与反推最晚可行开始两条相反依据链），该版本**不得定稿**。冲突按成因归因：
  - `invalid_range`：输入范围自相矛盾（最早开始晚于最晚开始）；
  - `cycle`：“先于”关系构成**真正不可满足的有向环**（沿关系方向兜圈）；
  - `window`：时间窗冲突，含**多路径上下界矛盾**——关系图本身无有向环，但最短等待
    沿一条路径推出的最早界与最长等待沿另一路径推出的最晚界无法同时满足。此时按
    受影响事件给出由输入和各关系推出的最早、最晚界及可读依据链，不再误报为关系成环。
- **最小冲突约束分析（本轮新增）**：在任意**可回看版本**的冲突结果中可进入分析；
  API 基于该版本不可变快照只读重建差分约束，定位一组足以单独造成冲突、且不可再
  缩减的约束（minimal unsat set）：整组不可满足，移去其中任一条后该组可满足。
  约束覆盖事件最早/最晚开始边界，以及每条“先于”关系的最短、最长等待（均含前项
  持续时间）。同一版本重复查询返回稳定的约束 ID 与组 ID；无冲突返回空组。暂存
  事件及其被跳过的关系不进入分析，并逐项说明排除原因。带时区约束换算为 UTC
  绝对分钟判断，响应仍保留当地时间与 UTC 偏移；未带时区沿用朴素本地时间语义。
  分析不保存任何内容，不改变当前输入、版本号或定稿状态；缺快照的旧版本返回
  422 明确拒绝，版本/复盘不存在分别返回 404。
- **暂存事件**：缺少时间范围（或持续分钟数）的事件可暂存，其关联的“先于”关系
  暂不参与推算，并在页面上标明原因；存在暂存事件时同样不得定稿。
- **多人协作并发控制**：基于 `reviews.version` 的乐观锁。携带旧版本号的提交会被
  拒绝（HTTP 409）并提示刷新，不会覆盖他人较新的记录。
- **定稿**：推算通过且无暂存事件时可定稿；定稿后再次修改会自动回到草稿状态。
- **跨地区事故时区（本轮新增）**：事件可指定 **IANA 时区**（如 `Asia/Shanghai`、
  `Europe/London`、`America/New_York`，留空 = 不指定时区）。指定时区后，
  已填写的最早、最晚开始必须是**带 UTC 偏移的当地时间**（如
  `2026-11-01T01:30-05:00`），偏移须符合该时区当日规则：
  - 夏令时**回退的重复时刻**（同一面盘时间出现两次）按所填偏移区分是哪一次，
    两次都合法（例如纽约 2026-11-01 01:30 可填 `-04:00` 或 `-05:00`，对应
    不同的绝对时刻）；
  - 夏令时**向前跳过、不存在的当地时刻**（如纽约 2026-03-08 02:30），或与
    当日规则不符的错误偏移、漏填偏移，都会让**整次保存被拒绝（HTTP 400）**，
    不写入任何记录。
  - 带时区事件的关系按**绝对分钟**（UTC 时刻）施加持续时长与最短、最长等待；
    输入区间先后也按绝对时间判断（与各事件所在时区、当日夏令时无关）。推算
    结果与依据链按**各事件当地时间和偏移**展示（窗口另带该事件的 IANA 时区）。
  - **带时区与未带时区事件之间不得建立“先于”关系**，服务端明确报错（HTTP 400）；
    未指定时区的现有输入、输出保持既有朴素本地时间语义不变（且不允许携带偏移，
    以免静默改变旧语义）。
  - 保存、版本回看、恢复、对照均**完整保留时区与偏移，不改写任何旧快照**；
    冲突、暂存、定稿规则与过期版本的 409 行为保持不变。
  - 网页事件表新增「时区(IANA)」列（常用时区可下拉选择，也可手填任意 IANA 名），
    指定时区后时间框输入带偏移的当地时间，结果、版本回看与对照弹窗均显示时区。
- **跨复盘并入历史事件链（本轮新增）**：可把**另一份复盘某个可回看版本**的事件
  并入当前复盘。选定来源复盘、来源版本与来源事件集合后先**预览**：展示将复制的
  事件、**仅两端均入选**才会复制的“先于”关系，以及合并后的推算结论；**预览只读，
  不改任何版本**。确认时按目标 `base_version` 重新核对——目标未变化则推算结论
  必须与预览一致（由随预览返回、确认回传的 `digest` 校验），随后**一次性**保存为
  目标的**新草稿版本**：
  - 复制事件保留**时间范围、持续分钟、备注、IANA 时区及 UTC 偏移**；复制来的
    关系保留**最短/最长等待**；复制事件在目标取得**全新持续身份（uid）**，
    不沿用来源 uid；目标原有事件与来源复盘的历史均不变（跨复盘也不会按
    uid 配对，只复制输入本身）；
  - 来源与目标相同、入选事件不存在、来源版本缺少输入快照（旧库历史版本）、
    输入不合法一律拒绝（400/404/422）且**无部分写入**；目标版本过期沿用
    **409** 语义；合并结果有冲突或暂存事件时**仍可保存为草稿，但不得定稿**；
  - 并入产生的新版本在版本列表标注「并入产生」并记录来源复盘与来源版本，
    可正常回看、恢复、对照。网页在「历史版本」面板提供来源选择、事件勾选、
    预览与确认的完整操作。

## 容器启动

```bash
# 方式一：Docker Compose（推荐）
docker compose up --build
# 或旧版：docker-compose up --build

# 方式二：纯 Docker
docker build -t retro-timeline .
docker run -d -p 8000:8000 -v retro-timeline-data:/data --name retro-timeline retro-timeline
```

容器启动时自动初始化数据库（创建目录与表结构），无需手动迁移。

## 网页访问

启动后浏览器访问：**http://localhost:8000**（默认端口，见下方 `PORT` 配置）。

## 可配置项

| 环境变量 | 默认值（容器内） | 说明 |
|---|---|---|
| `PORT` | `8000` | 服务监听端口。Compose 中同时用于宿主机映射：`PORT=9000 docker compose up` 后访问 `http://localhost:9000` |
| `HOST` | `0.0.0.0` | 监听地址 |
| `DB_PATH` | `/data/timeline.db` | SQLite 数据库文件路径。容器内 `/data` 为挂载卷，删除容器数据不丢失 |

Compose 示例（`docker-compose.yml`）中已包含命名卷 `timeline-data` 用于持久化；
如改用宿主机目录，可将卷替换为 `./data:/data`。

## 本地开发（不用 Docker）

```bash
python3 app/server.py          # 默认监听 8000，数据库写入 ./data/timeline.db
PORT=9000 DB_PATH=/tmp/t.db python3 app/server.py
python3 tests/test_compute.py  # 推算引擎单元测试（含间隔上下界）
python3 tests/test_versions.py # 版本快照/回看/恢复、旧库兼容单元测试
python3 tests/test_api.py      # HTTP API 端到端测试（间隔校验、409、恢复保留间隔）
python3 tests/test_compare.py  # 版本对照测试（身份配对、差值、只读、错误路径）
python3 tests/test_timezone.py # 跨地区时区单元测试（夏令时 gap/重复时刻、绝对分钟推算、当地展示）
python3 tests/test_api_timezone.py # 跨地区时区 HTTP API 端到端测试（400 拒绝、回看/恢复/对照保留时区）
python3 tests/test_merge.py    # 跨复盘并入规划单元测试（两端入选关系、字段保留、新身份、digest）
python3 tests/test_merge_db.py # 并入持久化测试（预览只读、确认新草稿、409/400、冲突暂存不得定稿）
python3 tests/test_merge_api.py # 并入 HTTP API 端到端测试（预览/确认、错误路径、时区偏移保留）
python3 tests/test_analysis.py # 最小冲突约束单元测试（MUS、暂存排除、UTC 绝对分钟）
python3 tests/test_analysis_api.py # 最小冲突约束 HTTP API 测试（只读稳定、404/422）
```

> 跨地区时区依赖系统 IANA 时区库（Python 标准库 `zoneinfo` 读取
> `/usr/share/zoneinfo`）。容器镜像已通过系统包 `tzdata` 内置；在缺少时区库的
> 精简 Linux 上可安装系统包 `tzdata`（Debian/Ubuntu：`apt-get install tzdata`），
> Windows 上本地开发可使用 `pip install tzdata`。服务本身不引入任何第三方依赖。

## HTTP API 简介

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/reviews` | 复盘列表 |
| POST | `/api/reviews` | 新建复盘 `{title}` |
| GET | `/api/reviews/{id}` | 复盘快照：元信息 + 原始输入 + 最近一次推算结果 |
| PUT | `/api/reviews/{id}` | 整体保存并重新推算 `{base_version, title, events, relations}`；事件可带 `timezone`（IANA 名，带时区时时间须带 UTC 偏移），关系可带 `min_gap_minutes` / `max_gap_minutes`；版本过期返回 409，间隔非法/偏移非法/不存在的当地时刻/跨时区混建关系返回 400 |
| POST | `/api/reviews/{id}/finalize` | 定稿 `{base_version}`；有冲突或暂存事件返回 400 |
| GET | `/api/reviews/{id}/versions` | 版本列表（含回看/恢复标记；旧库无快照版本 `reviewable=false`） |
| GET | `/api/reviews/{id}/versions/{v}` | 版本回看：当时标题/事件/关系输入 + 推算依据与状态；旧库无快照返回 422 |
| GET | `/api/reviews/{id}/versions/{v}/analysis` | 基于版本不可变快照的最小冲突约束分析（只读）：无冲突返回空组；旧库无快照返回 422，版本不存在 404 |
| POST | `/api/reviews/{id}/versions/{v}/restore` | 恢复为新草稿版本 `{base_version}`；过期返回 409，旧库无快照返回 422 |
| GET | `/api/reviews/{id}/compare?from={v1}&to={v2}` | 两版本对照（只读）：须 `v1 < v2`；顺序无效 400、版本不存在 404、旧库无快照 422 |
| POST | `/api/reviews/{id}/merge/preview` | 并入预览（只读）：`{source_review_id, source_version, event_uids}`；返回复制事件、两端均入选的关系、合并后推算结论与 `digest`；来源/目标相同 400、事件不存在 400、来源缺快照 422、复盘或版本不存在 404 |
| POST | `/api/reviews/{id}/merge/confirm` | 确认并入，一次性另存为目标新草稿版本：`{source_review_id, source_version, event_uids, base_version, digest}`；目标过期 409、digest 与预览不符 400 |
| DELETE | `/api/reviews/{id}` | 删除复盘 |

### 版本回看与恢复（HTTP 示例）

```bash
# 1) 列出可回看的版本
curl -s http://localhost:8000/api/reviews/1/versions
# {"current_version": 4, "versions": [
#   {"version": 4, "kind": "finalize", "state": "finalized",
#    "reviewable": true, "restorable": true, "status": "ok", ...},
#   {"version": 3, "kind": "save", "state": "draft", "reviewable": true, ...},
#   {"version": 2, "kind": null, "reviewable": false, "restorable": false,
#    "reason": "历史版本缺少完整输入快照，不可回看、不可恢复", ...} ]}

# 2) 回看某版本：返回不可变输入（title/events/relations）与当时推算结果
curl -s http://localhost:8000/api/reviews/1/versions/3
# {"version": 3, "state": "draft", "kind": "save", "title": "...",
#  "input": {"events": [...], "relations": [...]},
#  "result": {"status": "ok", "windows": {...}, "conflicts": [], "drafts": []}}

# 3) 把 v3 恢复为当前复盘的新草稿版本（必须带当前 base_version；旧版本不被覆盖）
curl -s -X POST http://localhost:8000/api/reviews/1/versions/3/restore \
  -H 'Content-Type: application/json' -d '{"base_version": 4}'
# 成功返回与 GET /api/reviews/1 相同的新快照（version=5, status="draft"）。
# base_version 过期 → 409 {"error": "...", "current_version": 5}
# 恢复冲突/暂存输入：允许成功，但该新草稿仍不可定稿（finalize 返回 400）。
# 恢复旧库无快照版本 → 422 {"error": "历史版本缺少完整输入快照，不可回看、不可恢复"}
```

页面下方「历史版本」面板提供同样的操作：每行可**回看**（弹窗展示当时输入与推算依据
链，可直接**恢复为新草稿版本**）；旧库无快照版本只显示不可回看、不可恢复的原因。
定稿产生的新版本会标注「已定稿」并沿用同一输入；由恢复产生的版本标注「恢复产生」并
记录来源版本。

### 最小冲突约束分析（本轮新增，HTTP 示例）

对某个**可回看版本**只读分析其不可变输入快照，定位一组足以单独造成冲突、且不可再
缩减的约束。每个事件最早/最晚开始边界是一条约束；每条“先于”关系的最短等待（含
前项持续）和最长等待（含前项持续）也分别是约束。分析把这些约束写成差分边并检测
正权环；返回的组满足：**整组不可满足，删除其中任意一条后该组可满足**。

```bash
# 冲突版本：返回最小冲突组
curl -s http://localhost:8000/api/reviews/1/versions/3/analysis
# {
#   "review_id": 1, "version": 3, "status": "conflict",
#   "snapshot_status": "conflict",
#   "analysis_readonly": true,
#   "groups": [{
#     "id": "mus-4f2c8a91b0de", "type": "window",
#     "minimal_unsatisfiable": true,
#     "satisfiable_after_removing_any_constraint": true,
#     "related_event_ids": ["id_1", "id_2"],
#     "related_events": [{"id":"id_1","uid":"...","name":"A","timezone":null}],
#     "constraint_ids": ["e0001:id_1:latest_start",
#                        "e0002:id_2:earliest_start", "r0001:max_wait"],
#     "constraints": [
#       {"id": "e0001:id_1:latest_start", "kind": "event_boundary",
#        "bound": "latest_start", "local_time": "2026-10-01T11:00",
#        "time_basis": "naive_local", "timezone": null,
#        "edge": {"from": "id_1", "to": "__time_origin__", "weight_minutes": -...}},
#       {"id": "r0001:max_wait", "kind": "precedence", "bound": "max_wait",
#        "predecessor_duration_minutes": 30, "max_gap_minutes": 10,
#        "difference_minutes": 40,
#        "edge": {"from": "id_2", "to": "id_1", "weight_minutes": -40},
#        "message": "「A」先于「B」：前项持续 30 分钟、最长等待 10 分钟，后项开始至多只能晚 40 分钟"}
#     ]
#   }],
#   "group": { ...同 groups[0]... },
#   "excluded_draft_events": [],
#   "excluded_skipped_relations": [],
#   "exclusion_reason": "暂存事件缺少完整时间范围或持续时间；其关联关系随暂存事件一起跳过..."
# }

# 无冲突版本：空组（HTTP 200）
curl -s http://localhost:8000/api/reviews/1/versions/1/analysis
# {"status":"ok","groups":[],"group":null,...}

# 错误路径
# 版本不存在 → 404 {"error":"该版本不存在"}
# 复盘不存在 → 404 {"error":"复盘不存在"}
# 旧库版本缺少不可变快照 → 422 {"error":"历史版本缺少完整输入快照，无法分析冲突",
#                              "analyzable":false,"reviewable":false,"version":1}
```

带时区约束的 `time_basis` 为 `utc_absolute`，包含 `absolute_minutes`（UTC 绝对
分钟）、`timezone`、`utc_offset_minutes`，且 `local_time` 保留带偏移的当地时间；
未带时区约束为 `naive_local`，不换算 UTC。暂存事件列在
`excluded_draft_events`，因暂存或引用不存在事件而被跳过的关系列在
`excluded_skipped_relations`，两者都带 `reason` 与 `excluded_constraint_ids`。
页面上冲突卡片提供「分析最小冲突约束」按钮，版本回看弹窗与当前版本冲突结果均可进入。

### 版本对照（本轮新增，HTTP 示例）

选择同一复盘中较早（`from`）与较晚（`to`）两个版本对照，**只读**：不改动当前输入、
版本号与定稿状态。跨版本按事件持续身份 `uid` 配对——`uid` 由服务端在保存时下发
（`GET /api/reviews/{id}` 与保存响应的每个事件均携带），客户端下次保存时随事件回传
即可延续身份；恢复产生的版本沿用快照中的身份。改名、调整时间后仍识别为同一事件；
不回传 `uid` 的事件一律视为新事件，绝不按名称或行号猜配。

```bash
# 1) 对照 v2（较早）与 v4（较晚）
curl -s 'http://localhost:8000/api/reviews/1/compare?from=2&to=4'
# {"pairable": true, "title_changed": true,
#  "from": {"version": 2, "kind": "save", "state": "draft", "status": "ok", ...},
#  "to":   {"version": 4, "kind": "restore", "state": "draft", "status": "ok", ...},
#  "summary": {"events_added": 1, "events_removed": 0, "events_renamed": 1,
#              "events_changed": 2, "events_unchanged": 0,
#              "relations_added": 0, "relations_removed": 0, "relations_changed": 1,
#              "conflicts_from": 0, "conflicts_to": 0, "drafts_from": 0, "drafts_to": 0},
#  "events": {
#    "added":    [{"uid": "...", "name": "复盘会", "draft": false, "window": {...}}],
#    "removed":  [...],
#    "renamed":  [{"uid": "...", "from": "告警触发", "to": "告警触发（pager）"}],
#    "changed":  [{"uid": "...", "name": {"from": ..., "to": ...},
#                  "input_changes":  {"earliest_start": {"from": "...", "to": "..."}},
#                  "window":         {"from": {...}, "to": {...}},
#                  "window_changes": {"earliest_start": {"from": "...", "to": "..."}},
#                  "draft": {"from": false, "to": false}}],
#    "unchanged": [{"uid": "...", "name": "..."}]},
#  "relations": {"added": [...], "removed": [...],
#    "changed": [{"predecessor": {"uid": "...", "name": "告警触发"},
#                 "successor":   {"uid": "...", "name": "值班响应"},
#                 "from": {"min_gap_minutes": 10, "max_gap_minutes": 60},
#                 "to":   {"min_gap_minutes": 20, "max_gap_minutes": null}}]},
#  "conflicts": {"from": [], "to": [{"type": "window", "message": "...",
#                                    "events": [{"uid": "...", "name": "..."}]}]},
#  "drafts": {"resolved": [...], "became": [...], "remaining": [...],
#             "from_count": 0, "to_count": 1}}

# 2) 错误与降级
curl -s 'http://localhost:8000/api/reviews/1/compare?from=4&to=2'
# 400 {"error": "较早版本必须小于较晚版本（from=4 不早于 to=2）"}   # 顺序无效
curl -s 'http://localhost:8000/api/reviews/1/compare?from=2&to=99'
# 404 {"error": "该版本不存在"}
curl -s 'http://localhost:8000/api/reviews/1/compare?from=1&to=2'   # v1 为旧库无快照版本
# 422 {"error": "历史版本缺少完整输入快照，无法比较", "comparable": false, "version": 1}
# 有快照但缺可靠身份（本轮升级前保存）的版本：200 且 pairable=false，
# 响应仅含 from/to 两版的 input 与 result 原貌，不含 events/relations 差值。
```

页面上在「历史版本」面板上方的对照栏选择较早/较晚版本后点「对照这两个版本」即可
查看同样内容（缺快照的旧版本在下拉中禁选）；对照弹窗分组展示新增/删除/改名事件、
输入与推算窗口变化表、关系间隔变化、暂存状态变化与两版冲突对比。

### 跨复盘并入历史事件链（本轮新增，HTTP 示例）

把另一份复盘某个可回看版本的事件并入当前复盘。事件按来源版本中的**持续身份
`uid`**选取（`GET /api/reviews/{source_id}/versions/{v}` 的 `input.events[].uid`，
或来源复盘 `GET /api/reviews/{source_id}` 的 `events[].uid`）。预览**只读**，
确认后一次性另存为目标的新草稿版本。

```bash
# 1) 预览（不改任何版本）：选择来源复盘 1 的 v2，并入其中两个事件
curl -s -X POST http://localhost:8000/api/reviews/2/merge/preview \
  -H 'Content-Type: application/json' -d '{
    "source_review_id": 1, "source_version": 2,
    "event_uids": ["afc915d1fe5c4e40909bb8eed91ef977",
                   "9253c0c2e2ce429489559a483015f640"]}'
# {
#   "target": {"review_id": 2, "title": "目标复盘", "base_version": 2, "status": "draft"},
#   "source": {"review_id": 1, "title": "来源复盘", "version": 2, "kind": "save", ...},
#   "copied_events": [
#     {"uid": "afc915...", "name": "来源告警",
#      "earliest_start": "2026-10-01T09:00", "latest_start": "2026-10-01T09:00",
#      "duration_minutes": 15, "note": "源备注", "timezone": null},
#     {"uid": "9253c0...", "name": "来源响应",
#      "earliest_start": "2026-10-01T09:00", "latest_start": "2026-10-01T12:00",
#      "duration_minutes": 30, "note": "", "timezone": null}],
#   "copied_relations": [
#     {"predecessor": {"uid": "afc915...", "name": "来源告警"},
#      "successor":   {"uid": "9253c0...", "name": "来源响应"},
#      "min_gap_minutes": 10, "max_gap_minutes": 60}],     # A→未选事件 的关系不会出现
#   "result": {"status": "ok", "windows": {"t_3": {...}, "s_afc915...": {...}, ...},
#              "conflicts": [], "drafts": [], "skipped_relations": []},
#   "digest": "9f2c…（合并输入的核对值）"}

# 2) 确认：携带目标当前 base_version 与预览返回的 digest
curl -s -X POST http://localhost:8000/api/reviews/2/merge/confirm \
  -H 'Content-Type: application/json' -d '{
    "source_review_id": 1, "source_version": 2,
    "event_uids": ["afc915d1fe5c4e40909bb8eed91ef977",
                   "9253c0c2e2ce429489559a483015f640"],
    "base_version": 2, "digest": "9f2c…"}'
# 成功返回与 GET /api/reviews/2 相同的新快照（version=3, status="draft"）。
# 复制事件保留时间范围/持续/备注/时区与 UTC 偏移；在目标 events 中带有全新 uid；
# 关系保留等待上下界；目标原有事件、来源复盘历史均不变。

# 3) 错误路径
# 目标在此期间被他人保存 → 409（沿用既有冲突语义）
# 409 {"error": "该复盘已被其他成员保存了更新的版本…", "current_version": 3}
# base_version 新但 digest 仍是旧预览的（目标内容已变）→ 400
# 400 {"error": "合并内容与预览不一致（目标可能已重新保存…），请重新预览后再确认"}
# 来源与目标相同 → 400；入选事件不存在/空集合/重复/类型错误 → 400（均无部分写入）
# 来源复盘或来源版本不存在 → 404；来源版本缺输入快照（旧库历史版本）→ 422
# 422 {"error": "来源版本缺少完整输入快照，不可并入", "mergable": false, ...}
```

预览结果 `result.windows` 的键：`t_<目标事件行id>` 是目标原有事件，
`s_<来源uid>` 是本次将复制的事件（确认后这些复制事件会拿到目标的新行 id 与
新 uid）。合并结果若为冲突或含暂存事件，确认仍会成功保存为新草稿，但
`finalize` 照旧返回 400。并入产生的版本可回看，其快照类型为 `merge`，
版本列表条目中带 `source_review_id` 与 `source_version`。

### “先于”关系的间隔设置（HTTP 示例）

每条关系可携带 `min_gap_minutes`（最短等待，非负整数分钟，缺省 0）与
`max_gap_minutes`（最长等待，非负整数分钟，可空/缺省 = 不限）：

```bash
# 1) 保存：告警结束后 10~60 分钟内值班必须响应
curl -s -X PUT http://localhost:8000/api/reviews/1 \
  -H 'Content-Type: application/json' -d '{
    "base_version": 1, "title": "支付故障复盘",
    "events": [
      {"key": "a", "name": "告警触发",
       "earliest_start": "2026-10-01T10:00", "latest_start": "2026-10-01T10:30",
       "duration_minutes": 15},
      {"key": "b", "name": "值班响应",
       "earliest_start": "2026-10-01T10:00", "latest_start": "2026-10-01T12:00",
       "duration_minutes": 30}
    ],
    "relations": [
      {"predecessor_key": "a", "successor_key": "b",
       "min_gap_minutes": 10, "max_gap_minutes": 60}
    ]}'
# 推算同时施加间隔上下界：
#   值班响应可行窗口 10:25 ~ 11:45
#   依据链示例：「告警触发」先于「值班响应」且「告警触发」持续 15 分钟、
#   最短等待 10 分钟 ⇒ 「值班响应」最早开始不早于 2026-10-01T10:25

# 2) 非法间隔被拒绝（HTTP 400），记录保持不变
curl -s -X PUT http://localhost:8000/api/reviews/1 \
  -H 'Content-Type: application/json' -d '{
    "base_version": 2, "title": "支付故障复盘", "events": [...],
    "relations": [{"predecessor_key": "a", "successor_key": "b",
                   "min_gap_minutes": 60, "max_gap_minutes": 10}]}'
# 400 {"error": "关系「告警触发」先于「值班响应」的最长等待不得短于最短等待"}

# 3) 版本回看/恢复保留每条关系当时的间隔设置
curl -s http://localhost:8000/api/reviews/1/versions/2
#  "input": {"relations": [{"predecessor_key": "id_1", "successor_key": "id_2",
#                           "min_gap_minutes": 10, "max_gap_minutes": 60}], ...}
curl -s -X POST http://localhost:8000/api/reviews/1/versions/2/restore \
  -H 'Content-Type: application/json' -d '{"base_version": 3}'
# 新草稿版本的关系同样带 min_gap_minutes=10, max_gap_minutes=60；旧版本不被覆盖。
```

不设置间隔的关系（包括升级前保存的旧关系）按 `min_gap_minutes=0`、
`max_gap_minutes=null` 处理，推算结果与旧版完全一致。间隔约束与输入范围冲突时
（例如最长等待小于前项结束到后项最早开始的距离），推算状态为 `conflict`，
冲突明细中给出冲突事件与正/反两条依据链，输入保留但不得定稿。

### 跨地区事故时区（本轮新增，HTTP 示例）

事件通过可选的 `timezone` 字段指定 IANA 时区；一旦指定，`earliest_start` /
`latest_start` 必须是带 UTC 偏移的当地时间。推算在内部全部换算为绝对分钟，
返回结果按每个事件自身的当地时间与偏移展示，窗口对象额外带 `timezone` 字段。

```bash
# 1) 上海 10:00(+08:00) 告警，纽约团队响应；A 先于 B（A 持续 15 分钟）
curl -s -X PUT http://localhost:8000/api/reviews/1 \
  -H 'Content-Type: application/json' -d '{
    "base_version": 1, "title": "跨地区支付故障",
    "events": [
      {"key": "a", "name": "上海告警", "timezone": "Asia/Shanghai",
       "earliest_start": "2026-10-01T10:00+08:00",
       "latest_start":   "2026-10-01T10:00+08:00",
       "duration_minutes": 15},
      {"key": "b", "name": "纽约响应", "timezone": "America/New_York",
       "earliest_start": "2026-09-30T20:00-04:00",
       "latest_start":   "2026-10-01T12:00-04:00",
       "duration_minutes": 30}
    ],
    "relations": [{"predecessor_key": "a", "successor_key": "b"}]}'
# windows 中 B 的最早可行开始按纽约当地时间展示（= 上海 10:15+08 即 02:15 UTC）：
#   {"2": {"timezone": "America/New_York",
#          "earliest_start": "2026-09-30T22:15-04:00",
#          "latest_start":   "2026-10-01T12:00-04:00", ...}}
# 依据链同时出现两地时间：「上海告警」…2026-10-01T10:00+08:00 ⇒
#   「纽约响应」最早开始不早于 2026-09-30T22:15-04:00

# 2) 夏令时回退的重复时刻：纽约 2026-11-01 01:30 出现两次，
#    -04:00（第一次，05:30 UTC）与 -05:00（第二次，06:30 UTC）都合法
curl -s -X PUT http://localhost:8000/api/reviews/1 \
  -H 'Content-Type: application/json' -d '{
    "base_version": 2, "title": "跨地区支付故障",
    "events": [{"key": "b", "name": "纽约响应", "timezone": "America/New_York",
       "earliest_start": "2026-11-01T01:30-04:00",
       "latest_start":   "2026-11-01T12:00-05:00",
       "duration_minutes": 30}], "relations": []}'

# 3) 不存在的当地时刻（纽约 2026-03-08 02:30 因春令时被跳过）→ 整次保存 400
# 400 {"error": "事件「纽约响应」的最早开始时间：当地时间 2026-03-08T02:30
#   在时区 America/New_York 不存在（处于夏令时切换被跳过的时段），请改用真实
#   存在的当地时刻"}

# 4) 错误偏移（7 月纽约是夏令时 -04:00，却填 -05:00）→ 整次保存 400
# 400 {"error": "…时间 2026-07-01T21:00 携带的偏移 UTC-05:00 不符合时区
#   America/New_York 当时的规则（应为 UTC-04:00）"}

# 5) 漏填偏移 → 400；未指定时区却携带偏移 → 400；非法 IANA 名 → 400
# 6) 带时区与未带时区事件之间建立“先于”关系 → 400
# 400 {"error": "带时区事件与未带时区事件之间不得建立“先于”关系：「上海告警」
#   带时区，「本地排查」未带时区；请为两端统一指定或清空时区"}
```

保存成功后，时区与偏移会随事件持久化并进入该版本不可变快照：
`GET /api/reviews/{id}/versions/{v}` 的 `input.events[].timezone` 与带偏移时间、
`result.windows[].timezone` 与当地窗口均可回看；`restore` 另存的新草稿沿用同样
的时区与偏移；`compare` 会把 `timezone` 作为事件输入变化的一列（同一持续身份
`uid` 改了时区会在 `input_changes.timezone` 体现）。不带 `timezone` 的旧事件、
旧快照继续按朴素本地时间处理，旧快照不被改写。

### 多路径上下界矛盾的冲突归因（本轮变动）

关系图无有向环、但多条路径的间隔上下界相互矛盾时，推算**不再误报**
“先于关系构成循环”（`cycle`），而是按时间窗冲突（`window`）指出受影响事件，
展示由输入和各关系推出的最早、最晚界及可读依据链。真正有向环仍按 `cycle` 报告。

```bash
# 复盘：四个持续 0 分钟事件，A 固定 10:00，其余起始范围 10:00~12:00；
# A→B 最短等待 60 分钟、B→D 无额外等待、A→C 与 C→D 最长等待各 10 分钟。
curl -s -X PUT http://localhost:8000/api/reviews/1 \
  -H 'Content-Type: application/json' -d '{
    "base_version": 1, "title": "发布故障复盘",
    "events": [
      {"key": "a", "name": "A", "earliest_start": "2026-10-01T10:00",
       "latest_start": "2026-10-01T10:00", "duration_minutes": 0},
      {"key": "b", "name": "B", "earliest_start": "2026-10-01T10:00",
       "latest_start": "2026-10-01T12:00", "duration_minutes": 0},
      {"key": "c", "name": "C", "earliest_start": "2026-10-01T10:00",
       "latest_start": "2026-10-01T12:00", "duration_minutes": 0},
      {"key": "d", "name": "D", "earliest_start": "2026-10-01T10:00",
       "latest_start": "2026-10-01T12:00", "duration_minutes": 0}
    ],
    "relations": [
      {"predecessor_key": "a", "successor_key": "b", "min_gap_minutes": 60},
      {"predecessor_key": "b", "successor_key": "d"},
      {"predecessor_key": "a", "successor_key": "c", "max_gap_minutes": 10},
      {"predecessor_key": "c", "successor_key": "d", "max_gap_minutes": 10}
    ]}'
# 响应（摘录）：status="conflict"，conflicts 仅含 window 类型（无 cycle）：
#   {"type": "window", "event_ids": [<D 的 id>],
#    "message": "事件「D」推算后最早可行开始 2026-10-01T11:00 晚于最晚可行开始
#                2026-10-01T10:20，约束冲突",
#    "earliest_chain": [「A」输入的最早开始为 10:00 →
#      「A」先于「B」…最短等待 60 分钟 ⇒「B」最早开始不早于 11:00 →
#      「B」先于「D」… ⇒「D」最早开始不早于 11:00],
#    "latest_chain":  [「A」输入的最晚开始为 10:00 →
#      「A」先于「C」且最长等待 10 分钟 ⇒「C」最晚开始不晚于 10:10 →
#      「C」先于「D」且最长等待 10 分钟 ⇒「D」最晚开始不晚于 10:20]}
# windows 中 D 的窗口为 11:00 ~ 10:20（被击穿）；输入保留，finalize 返回 400。
# 网页与版本回看（GET /api/reviews/1/versions/2）展示同一结论。
```

推算仍保留 `status`、`conflicts`、`windows`、依据链字段与 `base_version` 过期返回
409 的约定；保存冲突输入、禁止冲突版本定稿、暂存事件跳过推算的行为不变；
未设间隔的旧关系照旧推算，已有历史版本的回看内容不被改写。

## 目录结构

```
app/
  server.py          HTTP 服务（标准库 http.server）、间隔设置校验、时区/偏移校验、对照、并入与冲突分析路由
  db.py              SQLite 持久化、乐观锁、事件持续身份（uid）、启动时建表与老库迁移、并入事务、只读版本分析
  analysis.py        不可变快照的最小不可满足冲突约束分析（差分约束正权环与 MUS）
  merge.py           跨复盘并入的纯规划逻辑（复制选择、两端入选关系、合并推算与 digest；预览/确认共用）
  compute.py         时间窗推算与依据链生成（含间隔上下界传播、跨时区绝对分钟推算）
  tzutil.py          IANA 时区与带偏移当地时间校验（夏令时 gap/重复时刻）、按时区展示
  compare.py         版本对照：跨版本按 uid 配对与差值计算（纯函数，只读）
  static/            浏览器单页界面（原生 HTML/JS/CSS）
tests/test_compute.py  推算引擎单元测试
tests/test_versions.py 版本快照/回看/恢复与旧库兼容测试
tests/test_api.py      HTTP API 端到端测试
tests/test_compare.py  版本对照测试（身份配对、差值、只读、错误路径）
tests/test_timezone.py 跨地区时区单元测试（偏移/夏令时校验、绝对分钟推算）
tests/test_api_timezone.py 跨地区时区 HTTP API 端到端测试
tests/test_merge.py    并入规划单元测试（两端入选关系、字段保留、新身份、digest 稳定性）
tests/test_merge_db.py 并入持久化测试（预览只读、确认原子新草稿、409/400、冲突暂存不得定稿）
tests/test_merge_api.py 并入 HTTP API 端到端测试（预览/确认、错误路径、时区偏移保留）
tests/test_analysis.py 最小冲突约束单元测试（边界/等待/正环、缺一不可、暂存排除、时区）
tests/test_analysis_api.py 最小冲突约束 HTTP API 测试（只读稳定、空组、404/422）
Dockerfile / docker-compose.yml / .dockerignore
```
