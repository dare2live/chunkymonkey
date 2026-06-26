# §9 reference.duckdb 拆库 — 对抗验证版执行 spec (2026-06-26)

> owner: 主会话 (控制面). 触发: 用户 ultracode 授权 de-risk §9; workflow wxs0iyxin (8 agent / 670k tokens) 4-facet 审计→综合 Stage 计划→3-lens 对抗验证 (全 plan_sound=false) + controller 2 进程锁实测裁决。
> 关联: data_module_architecture_20260624.md §9.5 (原计划) + goal.md Active Priority Board 阶段一。
> **核心结论: §9 前提成立 (拆库真解耦主写锁痛), 但它不是"搬 4 张表", 是"4 套并行连接模型的收口" —— 原计划 (view+ATTACH on get_conn) 会在 ≥8 处炸生产。需 focus session, 先做连接收口 Phase 0 再切 view。**

## 1. 前提裁决 (controller 2 进程锁实测, architect rule7 亲核验证者)

§9 动机 = 拆 reference 出去, dim 读与 facts 写锁解耦 (根治: 回填写 facts 撞 universe 读)。对抗 lens 声称"拆库无用, 锁只是从 smartmoney 搬到 reference" —— **部分 overstate, 实测裁决**:

| 测 | 场景 | 结果 | 含义 |
|---|---|---|---|
| A | smartmoney RW 持锁 + reference RO ATTACH 读 dim | **OK (5208行)** | 原始主痛 (facts写smartmoney vs universe读) 拆库后**真解耦** ✓ |
| B | reference RW 持锁 + reference RO ATTACH | **撞锁 (IO Error lock)** | 残留: dim**写**时 dim 读被阻塞 |

**裁决**: §9 前提**成立** —— 频繁/长的 facts-backfill 写锁不再阻塞 dim 读 (主痛消除)。残留 (测B) = dim 写 (refresh_active_a_stock_master / build_dim_listing_status, DELETE+INSERT 全表) 时 dim 读阻塞, 但 dim 写**罕见/短** (日级 universe/listing 刷新, 秒级), 非频繁长 backfill → 可接受。**值得做, 但不是原计划那么做。**

## 2. 真 blast 源 = 4 套并行连接模型 (不是搬表本身)

dim 表读/写散在 **4 套互不相通的连接模型**, 给 get_conn 加 ATTACH 只覆盖 1 套:

| # | 连接模型 | 代表 | dim 读法 | view 切换后 |
|---|---|---|---|---|
| 1 | `get_conn()` (db_connection.py:34) | 多数 service | open smartmoney, 裸 FROM dim_x | 需 get_conn 注入 ATTACH reference |
| 2 | 直连 `duckdb.connect(SMART_DB)` | data_audit:184 / build_dim_listing:119 / audit_delivery_readiness×2 / audit_data_completeness / audit_pit_coverage / check_sina_tdxhub_overlap (≥5+) | 裸 connect 后 FROM dim_x | **各自不带 ATTACH → SELECT 抛 Catalog Error** |
| 3 | 注入 conn `def fn(conn,...)` | universe / recommendation_universe / screening_engine / return_engine / regime_engine / capital_client / external_attention / institution_write / holder_availability / stock_graph_read / stock_trends_read / data_quality | caller 传入的 conn | ATTACH 取决于 caller 用的哪套工厂 — **任一 caller 自建 conn = 炸** |
| 4 | bestchoice `_attach_smart_db(con) AS sm` (compute.py:606) | compute.py L1266/1743/3039 / main.py / scripts | connect(MARKET_DB) + ATTACH smartmoney AS sm, `JOIN sm.dim_active_a_stock` | **从不挂 reference; view 跨 catalog 引用在发起连接的 catalog 集解析, 不随 sm 传递 → 实测抛 'schema reference does not exist'**。计划完全漏判, 选股/状态页运行时 500 |

**DuckDB 机制实证 (1.5.2)**: (a) view 落盘后, 带 ATTACH 的连接 SELECT 透明返回 ✓; (b) **任何不带 ATTACH 的 open (含第三方 DBeaver/离线 audit/跨 venv) SELECT 抛 Catalog Error = 磁盘级污染**; (c) cross-db view 不可写 (DELETE 'Can only delete from base table' / INSERT 'is not an table'); (d) **CREATE INDEX on view → 硬炸 'can only create an index on a base table'**; (e) `CREATE TABLE IF NOT EXISTS` 遇同名 view = 静默 no-op (不炸但不建真表)。

## 3. 对抗验证抓到的 ≥8 break-point (原计划漏判, 全实证)

| # | break-point | 证据 | 修法 |
|---|---|---|---|
| B1 | bestchoice/compute.py (第4套连接) JOIN sm.dim_active_a_stock | duckdb 1.5.2 复现 schema-error | 给 bestchoice 每个 _attach_smart_db 连接同步 ATTACH reference, 或 bestchoice 这套不切 view 单独评估 |
| B2 | schema_migrations.py:291 CREATE INDEX on dim_active_a_stock | 实测硬炸 | INDEX DDL 必移 reference 建库路径; smartmoney 侧删此 INDEX 语句 |
| B3 | schema_core.py:431/443 + primitives/ddl.py:129 CREATE TABLE IF NOT EXISTS dim_* | 静默 no-op 遮蔽 | DDL 物理移 reference; smartmoney get_conn 路径删这些 CREATE |
| B4 | ≥5 直连 read_only audit 脚本 (audit_delivery_readiness/audit_data_completeness/audit_pit_coverage/check_sina_tdxhub_overlap) | 不带 ATTACH 直连 | 逐个加 ATTACH 或改走带 ATTACH 工厂 |
| B5 | 注入-conn 消费方一大类 (capital_client/external_attention/institution_write/holder_availability/...) | caller 决定 ATTACH | 出**显式注入链 fan-in 清单**, 逐个确认 caller 走 Stage B 工厂 |
| B6 | DuckConn.attach 吞异常 (duck_adapter L160-166 try/except: warning) | §4.4 违规 | reference 是必需依赖 → ATTACH 失败必须 raise (非 silent warn); 锁竞争期否则静默无 reference → 运行时炸 |
| B7 | 读触发写: get_active_a_stock_codes cache 过期 → refresh_active_a_stock_master 写 dim | 读消费方瞬间变写方 | 所有调 get_active_a_stock_codes 的消费方算潜在写方, 写必须 repoint reference RW |
| B8 | check_universe_filter.py L8 gate 匹配新 CREATE VIEW dim_active_a_stock 行 | commit 闸 FAIL | view DDL 行加 `# rule-compliance: ok evidence=` |
| B9 | Stage E 删除工具 db_lifecycle.py 不存在 (疑 data_deletion.py) | rg 0 命中 | 落地前核对真实删除函数名 + deletion_record 写入路径 |

## 4. 机制决策 (原计划 view+ATTACH vs alias-routing, 二义未定 = 隐患)

- **view+ATTACH**: smartmoney 4表→view→reference; 每个读连接必须 ATTACH reference。覆盖全 4 套连接但要求每套都接 ATTACH + 磁盘污染风险 (B1/B4/磁盘污染)。
- **alias-routing (resolver)**: data_access 把 4 dim entity 的 db 别名 repoint reference; 走 resolver 的消费方直连 reference 物理库 (无 view, 无 cross-db crash, 最 Occam)。但**只覆盖 SERVE/resolver 路径**, 第2/3/4套连接不经 resolver。

**架构师裁决 [2026-06-26 Occam 分析定案, choice(a) 实测]**: **alias-routing 胜, 不走 view+ATTACH**。实证: (1) 4 dim 表**都不在 data_access** (= 现不走 SERVE 读层); (2) resolver 按 db 别名路由 (`connect_ro(alias)` = duck_connect(manifest.path_for(alias), read_only=True), resolver.py:19-26) → **把 dim entity 的 `db` 别名改 `reference` = resolver 直接 connect_ro reference, 无 view 无 ATTACH 无磁盘污染**。**§3 的 ≥8 break-point 绝大多数随之蒸发** (无 smartmoney view → 无 B2 CREATE INDEX 硬炸 / 无磁盘污染 / 无 B6 attach 吞异常 / 无静默假存在)。**残留硬点 = cross-db JOIN** (facts smartmoney × dim reference 同一 query) 仍需 ATTACH 或拆成"先取 dim list 再 Python filter"; 但多数 dim 读是"取 universe 码 list / 查日历"(get_active_a_stock_codes 返 list) 非 in-SQL JOIN, 这些 alias-routing 干净。**真 Phase 0 = 把 4 dim 表纳入 data_access SERVE entity + 把 4 套连接的 dim 读点收口走 resolver** (= T0 "全读走 SERVE" 目标的一个切片, 见 §7)。

## 5. 修正后执行序 (focus session, controller-serial 写)

- **Phase 0 (NEW, 真前提, 大可逆重构)**: 连接模型收口 — 枚举全 4 套连接的 dim 读点 (复用 M5-T2 `lineage impact` + 注入链 fan-in 清单), 路由到单一带-ATTACH/reference-指向路径; 修 B6 (attach 失败 raise); 不切 view。**dual-write 中间态安全** (4表现仍在 smartmoney+reference 双副本, 读命中 smartmoney base table 不炸, 解耦收益未realize 但零破坏)。
- **Stage C (view 切换, escalate, 焦点独占)**: Phase 0 收口完 + 全 break-point 修完后, 逐表 rename base→_bak + CREATE VIEW→reference + 写方 repoint (含 B7 隐式写方) + B2/B3 DDL 移 reference + B8 注释。每切一张立即读冒烟 + revert-on-fail (DROP VIEW + rename _bak 回, 名字冲突注意)。
- **Stage D (验收, 修正口径)**: **测对的竞争** = 写 reference (刷 universe/日历) + 任意 get_conn ATTACH reference RO (原计划测 backfill+seed 是假绿, 那俩本不撞) + **bestchoice 状态页/选股链** + 全 pytest 套件。
- **Stage E (物删, escalate, 不可逆)**: 删 smartmoney _bak_ 4表; 先核 B9 删除工具真名; deletion_record; reference 成唯一真相源 (Stage A 建库副本作离线兜底)。

## 6. 安全可逆首步 (controller 可执行, 但建议焦点 session)

原计划 safe_first_step "get_conn 加 ATTACH" 被裁 **不安全** (3 lens 中 2 个判 safe_first_step_ok=false): 它同时改 data_audit:184/build_dim_listing:119 直连语义 (行为改动非纯挂载) + 漏 B6 (attach 吞异常先改才安全)。**真正的最小安全可逆首步 = 修 B6 (duck_adapter attach 失败 raise) + 出全 4 套连接的 dim 读点 fan-in 清单 (M5-T2 lineage impact + 注入链手工核)** —— 这是 Phase 0 的子步, 不碰 view, 完全可逆。但因 §9 整体高 blast + 触中央连接工厂, 建议整个 Phase 0+Stage C 在 fresh 焦点 session 做, 不在长 session 尾。

## 7. 元 flag (诚实)

- §9 是平台债 (解耦写锁 + 库分区), **非 alpha 钱路** — 蓝图 §6 优先级 T0(真金白银) >> 平台债。用户选平台优先 (合理战略) 但延后 alpha。
- 奥卡姆警示: view+ATTACH 多一层 = 多一个静默炸点 (磁盘污染/锁吞)。若 Phase 0 收口后发现 alias-routing 能覆盖绝大多数, 优先 alias (无 view 间接层)。
- **第一性原理终判 [2026-06-26 choice(a) 实测定案]**: 原始痛"回填读 universe 撞 facts 写锁"真因 = universe/calendar 表与 facts 同库 + **DuckDB 跨进程文件锁是进程级非连接级** (实测 1.5.2: smartmoney RW[进程A]持锁 → 进程B RO 打开直接 'Conflicting lock' 阻塞)。
  - **隔离 reader 连接 (Option B) 物理不可能解** — reader 进程根本打不开被 RW 锁的 smartmoney 文件, 无论它内部连接怎么结构。**拆库是唯一能解** (reference 独立文件, RO 与 smartmoney RW 不撞, 测A证)。**§9 拆库正当, Occam 没省掉它。**
  - **但机制 Occam 省了大头**: 不走 view+ATTACH (8 break-point/磁盘污染/魔法), 走 **SERVE alias-routing** (entity db 别名→reference, resolver 直连)。§3 break-point 绝大多数蒸发。
  - **§9 ≡ T0 (SERVE 收口) 的 dim 切片**: dim/universe/calendar 是 read-by-everything 最该走 SERVE 的表; 把它们收口走 data_access 后, reference 重定向 = 改个别名。§9 与 T0 在 dim 表上**汇流**, 是同一份收口工。
  - **本质 = "热读路径(universe/calendar)与写重路径(facts)解耦"** — 不是"DB 分区整洁", 是性能/健壮性修 (每次 backfill 不再阻塞全体 universe 读)。
