# check_legacy_flow_integrity.py / check_experiment_harness.py 收口 (2026-07-08)

## 背景

用户要求"做一个完整的针对数据底座的审计和测试"。16-agent对抗审计逐个核实14个 `safe_commit.sh` 挂载的治理门禁脚本, 抓出:
- `check_legacy_flow_integrity.py` 标 SUSPECT_VACUOUS(三条检查C1/C2/C3全部"空集合恒真")
- `check_experiment_harness.py` 标 CANNOT_VERIFY(脚本源码已随其守护的实验层一起被删)

用户要求"修, 还有 check_experiment_harness.py 怎么处理"。

## check_legacy_flow_integrity.py 精确诊断(推翻审计workflow的部分结论)

审计workflow把C1/C2/C3三条检查一并归为"SUSPECT_VACUOUS", 但逐条**红绿注入实测**(不只是静态读代码)后发现三者性质不同:

| 检查 | 审计workflow结论 | 实测复核结论 |
|---|---|---|
| C1 daily_update脚本可调 | 空集合恒真 | **真伪绿, 需修** — `scripts/daily_update.sh` 2026-06-23重设计为瘦wrapper(委托`python -m services.pipeline.run`), 该文件里不再直接提及任何`backend/scripts/*.py`路径, 继续扫它必然0命中, 与`db_lifecycle_delete.py::_live_surface()`此前踩过的同型漏洞(扫描源结构性排除真实调用图) |
| C2 无wiped表孤儿引用 | 空集合恒真 | **非伪绿, 检测逻辑真实有效** — 手工monkeypatch强制注入一个必然被真实引用的表名(`database_manifest`)当作wiped表, grep扫描立即真实抓到命中(`n_wiped=1`), 证明扫描机制本身完好; 当前`n_stale_refs=0`是"全部7张L2_feature表现均live"这一**真实项目状态**的正确反映, 非扫描目标空转 |
| C3 append-only必retention | 空集合恒真 | **非伪绿, 检测逻辑真实有效** — 注入一张假的`_history`命名表, `check_append_only_retention()`正确返回`FAIL`+`missing_retention`列表命中注入表名; 当前"0张`_history`/`_snapshot`表"是项目当前schema的真实状态 |

**结论**: 只有 C1 是真正的"结构性伪绿"(扫描源本身不含所需信息, 与目标是否存在无关); C2/C3 是"检测逻辑健全, 当前恰好无违规样本"——这是两种不同的失效模式, 不能因为"当前输出都是0"就笼统判定三者同等病态。区分这两种模式的方法是**注入实测**(往待验证目标里塞一个已知违规样本, 看检测逻辑能不能抓到), 不能只靠静态读代码猜测。

## 执行

### check_legacy_flow_integrity.py

1. C1 扫描源从 `scripts/daily_update.sh` 改为 `backend/services/pipeline/*.py`(真实调用图, 排除`__init__.py`), 正则不变(`backend/scripts/([A-Za-z0-9_]+\.py)`), 因为两种脚本调用嵌入方式(`ctx.run_script("backend/scripts/x.py", ...)` 与 `args = ["backend/scripts/x.py", ...]`)都是字面字符串, 同一正则可覆盖。
2. C2/C3 逻辑不变(已验证健全)。
3. Red→green验证: 临时在 `backend/services/pipeline/clean.py` 追加一行指向不存在脚本的引用, 门正确翻FAIL; 恢复后确认`git diff --stat`无残留。
4. 新增专属单测 `backend/tests/scripts/test_check_legacy_flow_integrity.py`(5个测试, 覆盖C1扫描源断言 + C1绿基线 + C1红绿注入 + C2检测逻辑活性验证 + C3红绿注入)。
5. 更新 `.moth/assertions/claims.yaml` 的 `legacy-flow-no-pollution` 断言: 文字描述从2026-06-14/16的历史叙事更新为准确反映当前状态; ratchet阈值从历史宽松值`n_stale_refs<=43`收紧到`==0`(当前真实状态), 回潮即红。

### check_experiment_harness.py

调研确认这不是"伪绿"而是**彻底的死代码**, 三重证据链:
1. 脚本本体 `backend/scripts/check_experiment_harness.py` 已随 2026-06-28 纯数据平台重建(commit `a078351e`)被git rm。
2. 它守护的harness目标(`services/phaseD_signal_eval.py`/`services/experiment_store.py`)同批一起被删——即便脚本还在也无处可指。
3. 它的owner文档 `docs/conditional_alpha_program.md` 同样不存在。
4. **双重确认它永不会再触发**: 它的触发条件(staged `backend/scripts/experiment_*.py`)现在被另一个正在生效的门`check_serve_read_layer.py` D4(feature-from-l2)硬性禁止(该规则明确不许`backend/scripts/`下存在`experiment_*.py`文件), 即便未来有人想复活这类脚本, 也会先被D4拦下, 走不到Step 3.7。

`scripts/safe_commit.sh` 原有 `-f backend/scripts/check_experiment_harness.py` 存在性守卫已让这块代码优雅跳过多日(非报错, 非伪装通过), 但留着一段确认永不可达的死代码块本身就是本次死代码普查一直在清理的对象。**执行**: 直接删除 Step 3.7 整个 `if [[ ... ]]; then ... fi` 可执行块, 替换为一行说明性注释记录退役依据(与该文件里 Step 3.5/3.6 已有的"历史退役注释"风格一致)。

## 补记: legacy-flow-integrity 从 informational 升为硬闸

`safe_commit.sh` 原有注释明确写着"check_legacy_flow_integrity 的 C1 子检查因架构变迁已空转 PASS...待 C1 修复后再考虑升硬闸"——C1 修好后, 按这条既定条件把 Step 3.994 从纯 informational(只打印不拦截)升级为真硬闸(`if...then...else exit 5;fi`, 与 Step 3.9 等其余硬闸同构)。`check_doc_governance`(Step 3.993, 因另一个独立原因——21条历史WARN backlog未清——仍保持 informational)未受影响, 未一并升级。

红绿验证: 临时在 `backend/services/pipeline/clean.py` 注入一处指向不存在脚本的引用, 复现 Step 3.994 真实报 `exit 5` 并打印具体错误; 撤销注入后确认 `git diff --stat` 无残留。

## 验证

全量测试568 passed(新增5单测); moth 29/0/0(修复claims.yaml文字后原coupling-no-orphan-refs一次性FAIL是因为该断言引用本文档路径而本文档当时还没创建, 创建后转绿); doc-drift/dead-references全绿; check_legacy_flow_integrity.py 三检查全PASS且非伪绿(逐条红绿实测); safe_commit.sh `bash -n` 语法校验通过 + Step 3.994 硬闸红绿验证通过。
