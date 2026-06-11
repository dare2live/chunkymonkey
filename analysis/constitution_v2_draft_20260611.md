# ChunkyMonkey 项目宪法 v2 (草案 2026-06-11)

> 最高权威: 代码/配置/流程与本文件冲突时, 本文件优先, 代码必须改. 修改需用户明确同意.
> v2 重铸依据: 奥卡姆审计 (76% commit 耗在文档/治理维护, 治理机器自我膨胀; 实战有拦截证据的只有 governance gate + plan_validator + review).
> 准入铁律: 每条条款必须带一次真实拦截或亏钱反例 (WHY), 否则进不了宪法 — 见第 10 条.

## 第一章 数据红线 (错了直接亏钱)

**第 1 条 PIT 零容忍.**
规则: 时刻 t 的决策只能用 <= t 已知信息 — JOIN 必带 `built_at/as_of <= t`, 盘后数据 (资金流/筹码) 特征取 t-1, 公告类锚 `ann_date` 不是生效日, selector/scoring 只读 `oos_*` 列且 NULL 不许 COALESCE 回 in-sample.
WHY: stage_opt 全期 MAX(oos_sharpe) 给历史信号用未来 Optuna 结果 → paper_sim "+312%" 假象 (commit 5cc47987).
验证: pit-audit 5 步 + `backtest_preflight.signal_pit_spotcheck` (截断未来数据重跑, 信号必须消失) + `code_leakage_scan`.

**第 2 条 异常数字双向警报.**
规则: RankIC>0.3 / sharpe>5 / 胜率>95% / 年化>100% 绝对红线, 相对 baseline 提升 >=50% 同触发 — 先怀疑泄漏后兴奋; 反向同理, 真实市场极值 != 坏数据, 告警阈值先问"物理上可能吗".
WHY: v3 RankIC "+75% 提升"实为 latest-snapshot + 99.978% sector fallback 双泄漏; 反向反例: 300085 八个精确 20% 连板 +453% 是真行情, 曾被 audit 误判坏数据.
验证: `governance.enforce_pre_insert` 拒收 + 触发后逐 col 群 ablation; 干净参考系 RankIC 0.011-0.020.

**第 3 条 真相源唯一.**
规则: 每个判断恰有一个真相源 (在交易=K 线有数据, 交易日=日历, 规则与阈值=YAML); 复制的常量、dataclass 默认值、平行 dim 表 = 第二真相源, 禁止.
WHY: dim_all_ever_listed 快照比对误标 573 只活跃股为退市; 旧印花税 10bps 在减半 3 年后还活在 default 参数里.
验证: `data_audit.cross_table_consistency` + 双独立路径 diff==0 互证 (如 cost round-trip vs label 层口径); 成本类 dataclass default-free 强制显式构造.

**第 4 条 Measured not estimated, unknown = NaN.**
规则: 任何参数/阈值/效果必须能指出"哪段 SQL + 哪个时间窗 + 几行真实历史"测出; 测不出标 unknown, unknown 必须是 NaN 让聚合自动排除, 严禁当 0 参与.
WHY: `swap_uplift_estimate` 公式估算掩盖了实测 swap 拉低年化 33pp; unknown 当 0.0 被 normalize 成合法值, 稀释 composite 并虚增合格类数 (降级期复查实锤, commit eee23138).
验证: 写数前四问 (哪段 SQL / 几行历史 / 换 unknown 决策会变吗 / 用户能复现吗) + NaN 行为断言进单测.

## 第二章 基础设施红线 (断流即失明)

**第 5 条 告警必须送达用户.**
规则: 定时任务必须包 wrapper (失败写 `/tmp/chunkymonkey_ALERT_*.flag` + 系统通知, 成功清 flag), 入口走有 FDA 的 launchd python, 禁止裸 cron; "装了定时任务"不算完成.
WHY: cron 无 FDA 每天 Operation not permitted 且静默, K 线断流 4+ 交易日无人知晓 (2026-06-11).
验证: 故意弄失败一次、告警真送达 = 安装完成的唯一标准; session 启动检查 ALERT flag.

**第 6 条 探活走协议层, 0 行当失败.**
规则: 数据源探活必须发真实协议包收响应 — 代理环境下 TCP connect 恒为 0.00s 假成功, 不作依据; writer 收到 0 行一律当失败入队重试 (<=3 次退避), 不静默落空.
WHY: Surge 接管全部 TCP 骗过 server 选择逻辑, 真实失败推迟到应用层读超时 (commit 81cbff7f); tushare 网关存在 15s 后 0 行不报错的间歇空响应模式.
验证: 连保留地址 192.0.2.1 "成功" = 代理接管实锤对照; failure_queue 表有迹可查.

**第 7 条 数据源优先序 (用户 2026-06-11 拍板).**
规则: tushare 主源 / tdxhub 备源 / miaoxiang 第三 / akshare 仅存量且持续退役; 路由只写在 `data_sources.yaml` capability 表, 新数据域接入只走 `sync_registry.yaml` 条目, 不写每域脚本.
WHY: tushare 239 接口实测 171 ok 且重叠日 K 线逐行 diff 验证口径一致; akshare 限频+改接口不稳定; 每域手写 sync 脚本是静默失败温床 (18 步覆盖缺口).
验证: 每 capability 必有 primary+fallback 条目; preflight 扫到 `services/` 新增 `*_sync.py` 直写表 = FAIL.

## 第三章 执行纪律 (跑了白跑就是烧钱)

**第 8 条 不验证不执行, 验证含运行时.**
规则: 跑批/回测/Optuna 前必过对应 gate (`plan_validator` / `backtest_preflight` / `data_audit` strict), FAIL 必须 raise/exit 阻断, 不许 WARNING 放行; 审计必须覆盖运行时实际加载, 不只查前置条件.
WHY: 29/34 公式无 search space 白跑 Optuna; DB 有 5206 股 preflight PASS, 但 runner 实际只加载 200 只且全是深主板.
验证: gate 全部代码化; 运行时 `validate_loaded_stocks` (四板块覆盖 + 80% 比例).

**第 9 条 防发散六 gate.**
规则: (1) 配置变体只许 base+diff override, 禁全量拷贝 yaml; (2) 建表先在 manifest 注册 grain + "为什么现有表不行"; (3) 禁第二套 driver/selector 引擎目录; (4) 策略分发走注册表, 禁 if-chain 加分支; (5) 对外数字必须 JOIN 到 `fact_sim_run` (status=complete, 含 config_hash+input_snapshot); (6) 新数据域必走 sync_registry.
WHY: 12 个 paper_sim yaml 全量拷贝 diff 记在注释里 / 推荐表 8 张同语义 / bestchoice 双引擎尸体 / selector 5 分支 if-chain / "+312%" 旁路出数 / 每域脚本静默失败 — 六种发散全都真实发生过.
验证: `config_divergence_lint` + `database_manifest` cross-check + architecture lint + rule_compliance PATTERNS + handoff run_id 存在性校验 + preflight 路径检查; 没有 gate 的规则 = 等于没立.

**第 10 条 治理自限 (奥卡姆对治理本身生效).**
规则: 宪法/流程条款准入门槛 = 至少一次真实拦截或亏钱反例; 文档义务收敛到两处 — goal.md (数字+决策+下一步) 与 PROJECT_INDEX 活索引; 工具规格说明书、配置清单表、分层叙事不入宪法.
WHY: 奥卡姆审计: 76% commit 耗在文档/治理维护而非策略与数据, 治理机器自我膨胀; 同期真正拦住错误的只有 governance gate、plan_validator 和 review.
验证: 每条款带反例 commit 可考; 季度复审, 12 个月无拦截记录的条款退役进附录.

## 第四章 协作纪律 (多 agent 时代的新坑)

**第 11 条 组间缝隙全局扫.**
规则: 并行修复按文件分 scope 后, controller 必须亲自做跨组全局扫 (`rg "FROM|JOIN" <泄漏源表>`), 不得只信各组 confirmed_fixed 的并集.
WHY: 同一泄漏源的多个消费点被切进不同组, 各组都"修完"、合起来仍漏 (2026-06 多 agent 修复工厂实战).
验证: 修复收尾 checklist 含全局 rg 输出记录; verifier 与 finder 不同盲区交叉 (同模型则同盲).

**第 12 条 降级期产物默认待复审.**
规则: 模型/verifier 降级期间合入的 PIT 类与默认值类改动, 恢复后必须重审; 代码注释声称的行为不等于实际行为.
WHY: 降级模型 + 降级 verifier 双盲放过 3 个真问题 (PIT/烧钱/unknown 静默参与), 恢复后复查才抓到 (commit eee23138); "注释写 unknown(0.0)" 验证者就信了 0.0 是 unknown.
验证: 降级期 commit 打标入清单; 恢复后逐项复查清零才解除标记.

**第 13 条 完成 = 可回溯的真实结果.**
规则: 完成 = 真实运行产出 + 对应 gate PASS + 数字可沿 `sim_run_id → strategy_id → model_id → formula_id → feature_group → source watermark` 链回溯; py_compile/lint 通过、单分数 improve 都不算完成.
WHY: LHB fact 没重建就说"完成了"; "+312%" 单分数无 evidence artifact 即噪音.
验证: 对外引用 KPI 必须有 `fact_sim_run` complete 行 + 三基准 (HS300/等权/不换股) 并排; 旧 validation artifact 只追加不覆盖.

## 附录 A — 退役条款 (v1 → v2)

| v1 条款 | 处置 | 理由 |
|---|---|---|
| 第四条 L0-L4 分层架构叙事 | 退役, 由六层契约注册制 (architecture_framework_design) 接管 | 分层文字从未拦截过一次错误; 拦截靠注册 yaml + gate, 不靠层次图 |
| 第六条 6.1-6.6 审计工具说明书 (~130 行规格) | 移出宪法 → 工具 docstring + PROJECT_INDEX | 规格书是 76% 文档 commit 的主力; 宪法只留第 8 条原则, 工具细节随代码走 |
| 第六条 session_handoff_audit 条款 | 降为普通工具, 不入宪法 | WARNING-only, 零阻断证据, 不满足第 10 条准入门槛 |
| 第七条 五项完成标准 | 收敛为第 13 条一句话 | 其中 3 项是文档义务, 与第 10 条文档收敛冲突 |
| 第九条 配置文件清单表 | 移至 PROJECT_INDEX 活索引 | 清单必然过期, 宪法不放会过期的东西; 配置驱动原则并入第 3/9 条 |
| 第八条 教训即规则 | 并入第 9/10 条验证方式 | 方向正确但无准入门槛, 实际助长了治理条款无证据增生 |
| 第二条 奥卡姆 (只对代码) | 升级为第 10 条 (对治理本身也生效) | v1 的剃刀只剃代码不剃自己, 结果治理层成了最肥的一层 |
| 第三条 模块+表+配置模式 | 并入第 9 条 gate (2)(6) | 原则没有 gate 时被 bypass 42 处, 证明叙述无效、gate 有效 |
