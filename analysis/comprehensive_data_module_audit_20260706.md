# 数据模块全面审计 — 2026-07-06

> 触发: 用户原话"数据完整性和连续性的问题已经整改无数次了...数据残留一直在清一直都有,
> 我实在是不放心开始后续工作"。本轮不是"再修几个点问题", 而是回答两个问题: (1) 现在数据
> 有没有正在发生的、未被发现的完整性问题? (2) 为什么残留会反复出现——是运气差还是机制性缺口?
>
> 方法: Workflow 9 维度并行审计 (investigate→独立复核 pipeline, 无 barrier), 55 agent /
> 1464 tool call / ~4.7M token, 每条 finding 都经过第二个独立 agent 用同样的命令重新实测复核
> (不信第一遍的 prose), 最后一个 completeness-critic agent 批判整个审计过程本身。
> 结论摘要见文末; 全部 findings 按严重度分层如下。

## 结论先行 (一句话)

**不能说"放心"——原因不是数据已经烂了, 而是验证数据没问题的机制本身还没被验证到位。**
本轮审计一出手就在"仅审计脚本/一个数据表"的小范围内抓到一个**当前正在发生、已持续 3 周、
现有门禁完全无感**的真实数据丢失 (stk_limit), 加上根因诊断证实"残留反复出现"是**同一类
系统性缺口反复触发** (非偶然)。详见下方分层。

---

## P0 — 立刻处理 (真实、当前、门禁看不见)

### 1. `raw_tushare_stk_limit` 60x/68x 板块静默丢数 ~29%, 已持续 3 周, 现有 gate 显示 PASS

**[HIGH, 双重独立验证 CONFIRMED]**

- 20260612: 5207 行 (built_at=同日 20:21, 实时写入)
- 20260615: 骤降到 3714 行, **但 built_at=2026-07-02**——证明这是**后来的一次回填批次写的**,
  不是当天实时同步
- 之后每天稳定卡在 3692-3714 行, 一直到 20260703, 而同期 `raw_tushare_daily` 稳定在
  5180-5200+ 行 (20260703 当天: daily=5193 vs stk_limit=3692, **缺口 1501 行**)
- 逐股验证: `603000.SH` 在 stk_limit 里有 2019-01-02 至 2026-06-12 连续 1804 行, **从
  20260615 起彻底消失**——和表级崩溃同一天
- 缺失代码前缀分布 (20260703): 603=613 / 688=607 / 601=175 / 605=112 / 689=1, 合计 1508,
  与实测缺口 1501 基本吻合——**精确命中沪主板新代码段 + 科创板**, 不是随机丢
- 这些前缀都在 `universe.py` 白名单 `('60','00','30','68')` 内, 排除是 universe 过滤导致
- **现有门禁确认失效**: `check_continuity_integrity.py --domain stk_limit` 跑出来是
  `overall=PASS pass=3 warn=0 fail=0`——即用户已经被告知"这个跑过了、干净"的那道门, 对这
  29% 的丢失**完全无感**。手工重放门禁内部算法 (`ROW_DIP_RATIO_DEFAULT=0.6`,
  20天滚动中位数) 发现崩溃后的比值precise地卡在 0.71(3714/5207), 刚好在 0.6 阈值之上不触发；
  更糟的是到 20260630 滚动中位数本身已经被拉低到 4460——**探测器在"自我校准到新的degraded常态"**,
  而不是报警
- `mart_data_source_failure_queue` 对 stk_limit 零记录; `min_rows_per_batch=3000` (本 session
  早些时候从 4000 下调, 为兼容合法的 2019 年历史小批次) 使得连崩溃后的 3692-3714 行批次也
  不会触发行数不足报警——**意味着我自己这次 session 早些时候的一次调整可能无意中放宽了
  对这个新问题的检测盲区**
- 下游已可见症状: `fact_stock_form_daily` 的 `buyable`/`sellable`/`is_one_word` 列自 20260615
  起每天约 1460-1500 行 NULL (20260703: buyable NULL=1469), 与 stk_limit 缺口几乎一一对应
  ——**当前约 1500 只股票的可交易状态在技术形态层是未知的**
- built_at=2026-07-02 这个时间点提示一个具体假设待查: 这 12 天的缺失可能是同一次回填操作
  写的, 有可能与本 session 早些时候那次"buyable NULL 根因修复"回填是**同一次操作**——
  即那次修复本身在解决旧问题的同时**留下了一个新的、不同的静默缺口**

**建议动作** (未执行, 待你拍板):
1. 立刻 `sync --domain stk_limit --start 20260615 --end 20260703 --drain` 回补缺失的
   60x/68x 代码
2. 查那次 07-02 回填的实际调用参数/日志, 定位是 page_limit 截断 / by_code_list 兜底逻辑
   出错 / 还是 vendor 网关对特定交易所前缀返回不全
3. 治理层面: `ROW_DIP_RATIO` 是所有 domain 共用的单一全局阈值, 不同表天然波动幅度差异很大;
   建议加一个"个股全市场表行数应约等于同日 daily 行数, 偏差>5% 报警"的**跨表覆盖率检查**,
   而不是只信一张表自己的滚动中位数(会被持续性缺口污染基线)

### 2. `check_dead_references.py` 结构性扫不到 SQL 字符串里的死表引用

**[HIGH, CONFIRMED]** — 这是回答"为什么残留反复出现"的关键机制之一, 见下方根因节。

---

## P1 — 根因诊断: 为什么残留反复出现 (回答用户核心问题)

审计的诊断结论是明确的 **(b) 同一类系统性缺口反复触发, 不是 (a) 每次全新偶然错误**:

| 证据 | 数字 |
|---|---|
| 全历史 commit 含"退役/物删/清理/根治"关键词占比 | 130/2179 = 6% |
| 最近 100 条 commit 同类占比 | 36/100 = 36%（**6 倍于历史均值**——清理工作正高度密集地反复发生） |
| 独立、时间跨度大、结构相同的"登记表烂掉从未被强制"案例 | 3 个不同子系统各一次 + 1 个正在发生 |

三个历史案例 + 1 个当场抓到的活案例, 结构完全相同——**登记类/审计类工件没有消费者、没有
CI 跑、只能靠人记得去查, 于是腐烂到 80%+ 死链才被一次性大扫除**, 而不是持续保鲜:

1. `check_panel_lineage.py`/`check_kpi_redlines.py` 引用的表在 2026-05-23 创建后 44 天
   从未被发现已经指向已删表 (本次审计当场实测崩溃复现)
2. `schema_versions.py` 173→17, 156 个死版本 (91%) 累积到 06-28 才一次性清 (commit `9b82d943`)
3. `test_tool_registry.yaml` 0 代码消费 + 82% 引用已删测试, 06-28 才退役
   (`docs/engineering_governance.md:95`)
4. **当场活案例**: `check_continuity_integrity.py`——本 session 刚新增的脚本 (commit
   `3033f067`)——**同样还没被 wire 进 safe_commit.sh 或 CI**, 独立复核 agent 发现这一点时,
   这正是评审自己说的"新脚本未同批注册"模式在此刻真实重演

**具体机制** (两条互相独立但共同作用的结构性盲区):

- `check_dead_references.py` 的 4 个扫描器 (import 语句/config yaml 路径/module=字面量)
  全部只处理 Python 符号引用和文件路径, **没有任何扫描器解析 .py 文件里的 SQL 字符串抓表名**。
  `check_panel_lineage.py` 引用死表的方式是 `conn.execute(f"SELECT ... FROM
  mart_p0b_lambdamart_v6_predictions")`——纯字符串, 结构性地不在检测范围内
- 退役类改动**没有标准化 checklist**——每次由执行者临时决定清理范围; 抽查历史退役 commit
  (`9b82d943`/`639e0dfb`) 发现 diff 本身诚实对应 commit message 声称的范围, **问题不是清理
  时偷懒漏做, 而是"清理范围"这个概念本身从一开始就没把 backend/scripts/check_*.py 这类旁支
  消费者纳入盘点对象**

**覆盖率而非"假绿放行"**: 独立复核纠正了一个初始判断——safe_commit.sh 里已接线的 9 道门
全部是硬阻断 (FAIL 即 abort), **不存在"门禁设计成可以带病 commit"的问题**; 真正的盲区是
21 个 `check_*.py` 脚本里只有 9 个被 safe_commit.sh 调用、CI 只多跑 1 个, 剩下 12 个
只能靠人工手动跑 (其中大部分连对应测试都没有)。复核进一步发现 4 个"未接线"脚本其实通过
另一层 `.git/hooks/pre-commit`/`commit-msg` 被覆盖, 实际盲区比初判小, 因此复核把这条的严重度
从 medium 修正为 low。

---

## P1 — 其他高优先级发现

| 维度 | 发现 | 严重度 |
|---|---|---|
| cross_db_consistency | `dim_active_a_stock`/`dim_all_ever_listed` 只能人工手动刷新, 当前已静默 stale 8-76 天 | high |
| cross_db_consistency | `sync_runner` "全批次成功才推进 watermark" 语义导致 watermark 冻结: `stk_factor_pro` 冻结 15 天, `block_trade` 冻结 **9.5 个月**——即使底层表数据其实更新 | high |
| stale_gate_scripts | `check_registry_promote.py` 用 `except Exception → WARN → exit 0` 吞掉"表不存在"——这个代码模式本身危险, 一旦被误接入 CI 会造成"门禁跑了、绿了、其实啥都没检查"的彻底静默失效 | high |
| stale_gate_scripts | `audit_storage_retention_consumers.py` 硬依赖 `rg` 二进制, 这台机器从未装过——4 个关联测试处于 SKIPPED 状态, 但 `docs/data_product_contract.md` 把它写成"清理前必须过的 blocking gate"——即过去所有"已跑消费方审计"的声明可信度存疑 | high |
| pit_leakage_spotcheck | `db_lifecycle_delete.py` 的删表前存活消费者扫描, 结构性排除 `backend/scripts/check_*.py` 目录本身——即删表工具压根不看治理脚本是否还在引用, 保证这类 bug 会按设计持续复发而非偶然 | high |
| test_coverage_gaps | 21 个 `check_*.py` 治理脚本里 11 个零测试覆盖, **且正是这 11 个从未被接入 safe_commit/CI**——审计原话"这正是残留反复出现的机制" | high |
| test_coverage_gaps | 本 session 刚提交的 NULL-safe DELETE 修复, 类型加宽逻辑只覆盖了 INSERT 路径, DELETE 阶段语句在某些加宽场景下可能仍会崩——需要针对我自己这次改动的直接跟进 | high |
| consumer_fanin_completeness | 20/49 (41%) 的 tushare_raw 表当前零业务消费方, 远超此前已知的 3 个 (express/forecast/moneyflow_hsgt), 且无机制区分"故意为未来特征层预拉取"和"真孤儿" | medium |

---

## P2 — 可接受、不紧急

- 三个已确认死引用但从未被 CI/safe_commit 调用的脚本 (`check_panel_lineage.py` /
  `audit_pit_coverage.py` / `check_kpi_redlines.py`)——纯手动脚本, 当前风险是"未来手跑会
  困惑", 不是"正在放行坏数据"
- `raw_tushare_stk_limit.pre_close` 列类型是 INTEGER (应为 DOUBLE), 99.38% 行 NULL,
  全仓库确认零代码读取它——确认是无害死列, 但对未来开发者是个地雷
- `sys_schema_version`/`excluded_stocks` 两个 0 行 DDL 化石表, 无 writer 也无 reader
  ——是用户担心的"两套矛盾排除名单"的反面: 根本没人用这套机制
- 928/2188 (42%) 全仓库孤儿 `.pyc` 文件跨 30 个目录 (gitignored, 无 CI 路径, 无害)
- `check_legacy_flow_integrity.py` 的 C1 检查因架构变迁已经"空转 PASS"——不是查出 0 问题,
  是没有东西可查了; 已确认它原本要防的风险 (删脚本不删调用方) 已转移到
  `backend/services/pipeline/*.py` 里的 6 个 `ctx.run_script()` 调用点, 目前这 6 个都存在,
  但 C1 的正则再也扫不到它们了
- `docs/chunkyctl_session_quickstart.md` (新会话首读文档) 仍引用一个已删脚本
  `audit_execution_surface.py`——虽是 medium, 但因为它出现在"新 session 第一件事读"的文档
  里, 实际造成的诊断浪费比评级更高, 建议优先顺手改掉这一行

---

## Completeness Critic 的独立结论

审计流程本身内置了一个批判性复核 agent, 在只拿到 2/9 维度完整传输内容的受限条件下
给出的判断 (原话摘要):

> **不该**对当前状态有信心。理由: (1) 仅在 2 个维度的小范围内就命中多个真实、confirmed 的
> 系统性缺陷, 命中率高说明问题密度大, 不是孤立个案; (2) 根因诊断指出的是**机制性缺陷**
> (验证范围窄于实际影响面), 机制性缺陷不会因为"这次修完了"就消失——同类残留会在下次表
> 重建/退役时以同样方式再现; (3) 支撑"过去清理已完成"这类声明的关键前置工具
> (`audit_storage_retention_consumers.py`) 在本机从未真正跑通过。**但同时要平衡说清楚**:
> 到目前为止确认的问题**没有一条是"当前生产数据流正在被污染"**——所有死脚本/假绿门禁都
> 已验证"未被任何现役路径调用", 影响面是"审计盲区"而非"数据正在变坏"。准确表述是:
> **不是"数据不能信", 而是"验证数据没问题的机制本身还没被验证到位"**。

---

## 方法论说明 (对这份审计本身的信任度)

- 每条 finding 都经过第二个独立 agent 用**同样的命令重新实测复核**, 不是读第一个 agent
  的 prose 就采信——3 条初判被复核推翻/降级 (safe_commit 覆盖率从 medium 降到 low, 因为
  发现了 git hooks 这层遗漏的证据; `data_layer_audit.py` MANAGED_DBS 数字从"4张"修正为
  实测"6张")
- Completeness critic 本身也诚实报告了它自己受到的限制 (只看到 2/9 维度的完整传输), 没有
  假装看到了全部就下结论——这本身也是一次"审计工具链是否可信"的正面样本

---

## 收口 (2026-07-06 同日执行, commit 见 git log)

用户批准两个方向"立刻查根因并回补" + "现在做机制修复"后的实际执行结果:

### P0 数据修复 (已完成, 已验证)
- `stk_limit` 根因: 全市场(股票+ETF+B股+北交所混合)总量增长跨过服务端隐式单页上限 (实测
  limit=6000 仍只回 5800, offset=5800 page2 再回 1877 行, 合计 7677)。加 `page_limit:5000`,
  回填 20260615-20260703 全部恢复 (对比 daily 表行数一致), 下游 `fact_stock_form_daily`
  全量 rebuild_all (6999725 行/1568 日/5434 码), buyable NULL 清零。
- **回填过程中意外抓到一个独立新 bug**: `run_domain` by_trade_date 分支的"跳过 watermark
  当天"判据在调用方显式传 `--start` 时恒真, 导致任何手工范围回填静默丢首日; 已修复 + 补
  red-green 回归测试。这个 bug 本身也印证了审计的核心诊断——即使是当天正在做的修复工作,
  也在持续产生新的、当场才发现的同类问题。

### 机制修复 (已完成, 已验证)
- `check_dead_references.py` 加第 5 道 SQL 字符串表名扫描 (scan_e), 已验证正确抓到
  `check_panel_lineage.py`/`check_kpi_redlines.py`/`check_registry_promote.py`/
  `audit_pit_coverage.py` 四个死引用治理脚本, 确认后全部物删。
- scan_e 本身在验证过程中暴露了 2 个自身 bug (均已修复 + 补测试): (1) 并发写锁占用时把
  "库暂时不可达"误判成"表不存在", 制造 64 处假阳性; (2) 正则把文档字符串里"禁止 FROM
  raw_*"这类规则说明误判成真实表名。这两个 bug 本身也是"审计工具需要被审计"的直接证据。
- `check_continuity_integrity.py` 接入 `safe_commit.sh` (此前"新增脚本未同批注册"的活
  案例, 现已解决)。
- `docs/engineering_governance.md` 加退役标准动作清单 (5 类消费者, 含"治理脚本 SQL 字符串
  引用"这个此前从未被纳入盘点范围的旁支)。
- 装 ripgrep, `audit_storage_retention_consumers.py` 从必崩溃变真正 PASS, 4 个关联测试转绿。
- 修 `docs/chunkyctl_session_quickstart.md` 里指向已删脚本 (`audit_execution_surface.py`)
  的那一行。

### 意外发现并处理的更大规模残留 (超出原计划, 用户逐步拍板)
- **`data_quality.py` 全模块 (3742 行) 零调用方**: scan_e 清出 27 处死引用后, 追查发现整个
  "全局数据质量门"子系统自 2026-06-28 策略层重建起就已死透 (文件自己的注释写着"这些
  guarded-no-op 检查函数可后续整体移除"——一条写好但从未兑现的"稍后处理", 正是审计诊断
  模式的实物证据)。用户问清楚"删了谁接管"后拍板"删": git rm 主文件, 唯一物化过的
  `mart_global_data_quality_gate` (128 行历史数据) 用 `db_lifecycle_delete.py` 归档
  (parquet 留底) 后物删, 清 4 处关联登记 (schema_versions/data_layers/
  data_module_members/experiment_jobs)。
- **但其中 `_check_calendar` 的日历前瞻余量检测被单独识别并保留**: 这个函数是 2026-07-03
  才建的真功能 (落实 sync_registry trade_cal 注释里那条"检查 max(cal_date)>today+30"的
  前瞻 SLA), 从建成起就没被真正接进任何跑批, 只有它自己的单测在验证它。与
  `static_staleness` 语义互补而非重复——一个测"多久没刷新"(往回看), 一个测"还能撑多远"
  (往前看); 即使日历"刚刷新过"也可能只覆盖到未来很浅, 静默限制任何"从今天起数 N 个未来
  交易日"的运算 (embargo/purge 窗口等) 悄悄少算而不报错。已迁移进
  `check_continuity_integrity.py` 新增 `calendar_horizon` 检测类型, 真正接进日常跑批
  (`run_checks` 全局单跑一次, 复用已加载的 `dim_trading_calendar` 全量列表, 不重复查库) +
  3 个新测试。
- **顺手补齐 `primitives` 模块缺失的 `dim_style_factor` 表**: 6 张姊妹 dim 配置表
  (dim_price_limit_rules 等) 都已存在于生产库, 只有这张没建成——判断为"该建但漏建"而非
  "已删残留", 直接调用其自身已设计好的 `ensure_primitives_tables`+`seed_style_factors`
  补建 (幂等, 零风险), 而非删除代码。
- **差点引入一次真回归, 已撤销**: 尝试"顺手简化" `audit_data_completeness.py` 对已退役
  `dim_data_asset` 的引用时, 一开始把它当"安全死代码"直接改成恒返回空——但深挖后发现这
  背后是一个真实、有测试覆盖的能力 (`coverage_policy`, 把"合法稀疏"表的 FAIL 降级成
  WARN), 差点在不知道有没有替代品的情况下删掉它。已撤销回原状态; 深挖后确认该能力已被
  `data_health_snapshot.py` 读 `data_layers.yaml` 的 `table_health_overrides` 取代 (非
  能力真空), 该引用连同 `storage_retention.py` 的 `mart_model_lifecycle` 引用 (2026-06-28
  U2/U5 就已明确记录为"non-breaking cosmetic, 奥卡姆不churn") 一起, 以**逐条验证过双重
  安全条件**(引用方守护代码不会崩 + 承担的能力已确认有替代或本就无害)的窄白名单形式保留
  在 scan_e 里, 而非批量豁免或仓促删除。

### 核心教训 (呼应用户的原始担忧)
审计工具越做越准, 挖出的问题就越挖越深——每一层新发现都需要重新判断"这是真残留(删)、
真功能(留/接进去)、还是未完工基建(补建)", 不能用同一把"发现引用死表就删"的尺子套所有
情况。今天的工作本身就是三次这样的判断分岔 (data_quality.py 整体删 / `_check_calendar`
单独留 / `dim_style_factor` 补建), 其中一次险些判断错误但被及时验证纠正。这正呼应了用户
最初的担忧: 光有工具和流程不够, 每一次具体的删除/保留决策仍然需要认真核查, 不能因为
"看起来符合模式"就批量处理。

全量测试 607 passed, `check_dead_references.py` 0 死引用 (含 2 条逐一验证过的窄白名单),
`db_compact` 缩盘 2.1G→1.7G。
