# SERVE 读层门系统性收口 (2026-07-08)

## 触发

用户问"数据地基是否具备切换主线条件", 深入核实四地基不变量时发现 `check_serve_read_layer.py` 的 D1(read-no-inline-table)/D2(read-no-self-asof)两道门硬编码只扫描 `backend/services/dossier.py` 一个文件——而 `dossier.py` 已随 2026-06-28 纯数据平台重建被 git rm(策略/serving 层整体退役, 不会再迁回), 读一个不存在的文件返回空字符串, 正则匹配空字符串必然 0 命中, 门永远绿但从未真正检查任何东西(pass-by-vacuity)。这与本项目 `db_lifecycle_delete.py::_live_surface()` 此前踩过的"结构性排除同型漏洞"是同一类失效模式。

用户明确要求"顶层设计, 系统性修复, 不要到处打补丁"。

## 调研发现

1. **原始设计从一开始就区分两种角色**(`analysis/data_module_toplevel_design_20260622.md` §2.2, 文档整体标 SUPERSEDED 但四不变量部分仍生效): **Builder(加工层)**——可直连 raw 源写复杂 SQL(JOIN/窗口函数), 归 build-time PIT 单测管; **薄消费者**——必须走 `DataAccess.get()`, 归 D1/D2 门管。`institution_profile.py`/`segments.py`/`market_pulse.py`/`technical_states/` 四个近期落地的 edge 模块全部是 builder(读 raw 做多表 JOIN + 窗口聚合, 物化产出 mart/feature 表), 不是消费者——这是设计意图, 不是漏洞。
2. **`DataAccess.get(entity, codes, as_of)` 本身设计上只支持单 entity 单表时间序列查询**(`GenericDriver._build()` 只生成 `SELECT cols FROM table WHERE code IN(...) AND asof<=t`), 没有 JOIN/窗口函数能力, 也不该有(§4.1 "零摩擦=加entity不改本体"的 god-module 红线)。四个 builder 模块需要的"多库 ATTACH + LAG() OVER + QUALIFY 去重"逻辑不可能塞进这个抽象, 硬改会违反本体简单性红线。
3. **项目自己早就发现过 D1 的伪绿问题**, 代码注释原文写着"D1 硬门只扫 dossier (P1 scope, 伪绿)", 并造了一个更完整的 `scan_consumer_bypass()`(原 `--bypass-scan` 参数): 全量扫 `backend/services`+`backend/scripts`, 读 `data_module_members.yaml` roster 正确区分 builder(可读raw)vs 非成员消费者(必须走 DataAccess)。四个模块在这道更完整的检查里全部合规, 因为它们逐一带日期+理由被登记为 builder。
4. **真正的缺口不是代码违规, 是两套检查没接对位置**: `scripts/safe_commit.sh` 每次 commit 真正跑的是只扫 dossier.py 的旧 5 道门(D1/D2 恒伪绿, D3/D4/D5 仍有效); 那道更完整、更正确的 `--bypass-scan` 检查只作为 `moth` 的一条断言(`serve-consumer-bypass-zero`)存在, 而 `moth assert` 从未接入 `safe_commit.sh` 或 CI——只有人工手动跑才会执行, 没人被强制跑。真正生效的 commit-time 硬门反而是那道伪绿的。

## 决策

不是"4个模块要改成走 DataAccess"(违背四模块的正确 builder 定位, 也超出 DataAccess 抽象能力), 而是"把检查机制本身修对":

1. **退役 D1(read-no-inline-table)/D2(read-no-self-asof)两道 dossier 专属门** —— dossier.py 永久消失, "P1 只迁 dossier" 这个历史 scope 已经不适用, 继续保留这两道空转的门只会掩盖真相。
2. **把 `scan_consumer_bypass()` 提升为默认执法的 D1**(取代原 D1/D2 职责), 不再需要额外的 `--bypass-scan` flag 才生效——现在 `PYTHONPATH=backend python backend/scripts/check_serve_read_layer.py`(无参数, 即 safe_commit.sh 已有的调用方式)默认就跑这道全量扫描。
3. **保留 D3(原D3 preflight-wired)/D4(原D4 lineage-complete)/D5(原D5 feature-from-l2)不变**, 仅重新编号为 D2/D3/D4。
4. **合并 `.moth/assertions/claims.yaml` 里两条重叠断言**为一条(`serve-read-layer-p1-doors`→`serve-read-layer-doors`, 删除已冗余且命令会因 `--bypass-scan` flag 移除而失效的 `serve-consumer-bypass-zero`)。
5. 更新 `check_serve_read_layer.py` 文件头注释 + `data_access.yaml`/`data_module_members.yaml` 里引用旧 flag/门编号的注释, 反映新结构。

## 验证

- **Red→Green 实测**(mio architect rule7 要求, 不能只看"0 violations"): 临时在 `backend/services/` 下放一个含 `FROM raw_tushare_daily` + `duckdb.connect(` 的假文件(未登记进 data_module_members.yaml), 门正确报 `[NO] D1 no-consumer-bypass` + `violations=1` + exit 1; 删除假文件后门恢复 `[OK]` + `violations=0` + exit 0。门真的在检测, 不是 print-not-fail。
- 全量测试/moth/dead-references/doc-drift 全绿(见 commit)。
- 四个 builder 模块(institution_profile/segments/market_pulse/technical_states)在新 D1 下继续合规(已在 `data_module_members.yaml` 正确登记), 未来任何新写的 edge/策略消费者代码如果内联裸查且未登记角色, commit 时会被真正拦下。
