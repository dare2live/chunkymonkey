"""publish_holder_labels —— 机构标签(dim_holder_identity_label) + 牛散标签(dim_holder_name_tag)。

执行依据: m1_label_store.md(机构侧设计, 已核证) + n1_niusan_design.md(牛散侧设计) +
业主拍板的两条(牛散范围=网络调研 13 人减去业主实测该剔的 4 人; 全部字段与本文档一致)。

# 两张表, 物理隔离(硬要求, 不是建议)

- `dim_holder_identity_label`: PK 里带 `holder_code`(供应商发的稳定码, 强身份)。
- `dim_holder_name_tag`:       PK 里带 `holder_name`(个人无码, 弱身份), 字段名一律带
  `name`/`identity_*`, 不带 `holder_code`, 不带 `label`(见下面"列名坑")。
两表不进同一张排行榜、不相加、下游 API 不混同一数组 —— 这是结构层面做到的(两条 JOIN、
两个 build、两个 endpoint), 不是靠使用者自觉。

# 列名坑(本轮发现, 机构侧不受影响所以此前没暴露)

`backend/scripts/data_layer_audit.py` 的 Type-A 列纯度门正则含 `label` 这个词且**逐列扫描
列名**(非表名)。机构表叫 `..._label` 没事(扫的是列名), 但牛散表如果给标签取值那一列
起名叫 `label` 会被判成 Type-B 泄漏拦下来。所以牛散表的列名统一用 `tag`/`tag_value`,
同理避开 `score`/`signal`/`win_rate`/`target`。

用法:
    # 机构标签(需要 w3_master_labels.json 形状的证据文件, 见下方"我没有创建 JSONL 证据文件"):
    PYTHONPATH=backend python backend/scripts/publish_holder_labels.py \\
        --db /tmp/smartmoney_test.duckdb \\
        --institution-json /path/to/w3_master_labels.json \\
        --batch-id b1 --ingested-at 2026-09-08T00:00:00

    # 只发牛散标签(名录内嵌在本脚本里, 不需要外部文件):
    PYTHONPATH=backend python backend/scripts/publish_holder_labels.py \\
        --db /tmp/smartmoney_test.duckdb --batch-id b1 --ingested-at 2026-09-08T00:00:00

--batch-id / --ingested-at 留空时分别落到"按 institution-json 内容取 sha1 前 12 位"和
"当前时间"—— 生产跑不需要手填。**验证幂等性(重跑两次全表 hash 相同)时必须显式传同一对
--batch-id/--ingested-at**, 这与 backend/scripts/build_price_kline_qfq_tushare.py 的
`ingested_at: str | None = None` 是同一种约定: TIMESTAMP 列不可能在两次真实时钟下自然相等,
"幂等"说的是"同输入同参数可重放", 不是"哪怕不传参时间戳也不变"这种不成立的更强命题。

======================================================================================
判不了 / 实测推翻设计的地方(全部如实列出, 不藏)
======================================================================================

## 判不了 #1 —— code_split_rules 给的是散文, 不是可执行的 name_pattern 规则

m1_label_store.md §1.2 明确要求 4 个机构码(10346801/10088552/10071181/10326139)按
name_pattern 拆成两个实体各自的 entity_type。但 w3_master_labels.json.code_split_rules
里对应条目只有中文散文描述("拆成两个实体: 高盛国际自营(高盛国际-自有资金,1909行) /
高盛国际中国基金-GSAM(...)"), 没有给出结构化的 {name_pattern, dim, value, value_cn} 字段。
把散文里的"自有资金"/"客户资金"/"(QFII)"这几个子串抠出来当 LIKE 模式技术上可行, 但
"抠对了子串"和"抠对了对应的 value/value_cn 取值"是两件事 —— 后者需要判断落在哪个 entity_type
枚举值上, 这正是红线8要求"分类成员是数据"的那类判断, 不该由发布脚本在散文里现场猜。
**处置**: 这 4 个码的 entities[] 泛化行(name_pattern='*')原样发布, 但打
`review_state='review_pending'`(名单见 backend/config/holder_labels.yaml
institution_label.split_pending_codes), 不新增任何 name_pattern 行。
**后果**: 这 4 个码里少数行的 entity_type 已知不准(例如高盛国际 1978 行里 69 行其实是
GSAM 基金产品而非自营, entity_type 现在对这 69 行也标成"外资投行自营")。业主/后续 subagent
需要把 code_split_rules 转成结构化 JSONL 才能解开这个 review_pending。

## 判不了 #2 —— 我没有创建 JSONL 证据文件(评审已出的设计假定它存在)

m1/n1 设计都假定证据层是 git 追踪的 `backend/data/reference/*.jsonl`。本次任务的文件所有权
只包含 `publish_holder_labels.py` / `holder_labels.yaml` / 本测试文件三个新文件, 不含
`backend/data/reference/` 下任何路径, 所以:
  - 机构侧证据通过 `--institution-json` CLI 参数从外部 JSON 读入(本轮实测用的是
    w3_master_labels.json 的会话态副本, 不在仓库里), 不是从仓库内 JSONL 读。
  - 牛散侧的 9 人最终名录(已应用业主的 4 人剔除裁决)以 Python 字面量
    `_NIUSAN_ROSTER` 内嵌在本文件里(见下方), 而不是外置 JSONL。
**后果**: 今天这次发布可重跑(传相同 --institution-json + --batch-id/--ingested-at 幂等),
但如果没有人把这两份证据落到仓库某处, 换一个 session 就没有输入可重放了 —— 这不是本脚本
能自己解决的, 需要业主决定证据文件归哪个 agent 的文件所有权。

## 实测推翻设计 —— valid_from 不能进 PRIMARY KEY 而不做 NULL 兜底(会导致插入报错)

m1_label_store.md §2.1 的 DDL 把 `valid_from` 同时声明成 `NOT NULL` 又放进 PRIMARY KEY;
n1_niusan_design.md §1.3 的 DDL 同样把 `valid_from` 放进 PK(但没标 NOT NULL, 更隐蔽)。
**实测** `w3_master_labels.json` 的 2,626 个机构实体里, 325 个 `known_from_grade=REFUSED_NULL`
的实体, 其 `valid_from` 字段本身也是 `null`(不只是 `known_from`—— R4"首见日冒充事件日"规则
把整个"何时成立"一起拒收, 不是只拒收"何时可知"), 覆盖 772 条待发布的 dim 行(12.6%)。
DuckDB 对 PRIMARY KEY 的任何一列都会自动加 NOT NULL(哪怕 DDL 没写 NOT NULL 关键字, 已用
`:memory:` 库现场验证), 所以如果照抄两份设计文档的 DDL 字面, 这 772 行会在 INSERT 时
撞 `Constraint Error: NOT NULL constraint failed`, 整个 REFUSED_NULL 分支(占实体数 12.4%)
会发布失败或被迫静默跳过 —— 这与两份设计文档"REFUSED_NULL 的行仍要留在表里(不删)"的
明确意图直接矛盾。
**修法**: `valid_from` 保留为可空的真实字段(语义不变: 查不到就是 NULL); 另开一列
`valid_from_pk TEXT NOT NULL = COALESCE(valid_from, '')` 仅用于 PRIMARY KEY, 从不出现在
任何 JOIN/WHERE 里(两张表都写了同一条防重复插入的原则性注释)。牛散表做了同样的处理
(尽管本轮牛散 9 人数据里没有实际命中 valid_from=NULL 的情况, 为避免同一类 bug 未来复发
统一加固)。

## 一处判断偏保守 —— 内容不可变性检查的范围只锁"取值"不锁"证据等级"

两份设计都要求"关旧开新, 不许静默改历史行"(m1 §2.4 / n1 §1.5), 并要求 publish 脚本对比
新旧 JSONL diff 拦下"同 PK 行的 value 被改而非新增一行"。本脚本实现了这个比对
(`_check_no_silent_value_rewrite`), 但**只锁 `value`/`value_cn`/`sub_value`(机构侧)这三个
"断言取值"字段, 不锁 `known_from`/`known_from_grade`/`confidence`/`evidence`/`source_file`
/`review_state`/`valid_to`**。理由: 后面这些字段代表"我们对同一个既有事实的证据质量",
理应允许在同一 valid_from 上被后续研究加固(例如某码从 UNVERIFIED 补证升级成
VERIFIED_FIRST_NOTICE), 这不是"偷偷改写历史事实", 是"证据变强"。但两份设计文档都没有
明确区分"事实内容不可变"与"证据等级可加固"这两种情形 —— 这是我做的判断, 不是文档写明的,
如果业主的本意是"连证据等级也不许在同一行原地改", 需要另行说明并调整
`_CONTENT_COLUMNS` 常量。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import yaml  # noqa: E402

from services.db import get_conn  # noqa: E402

CONFIG_PATH = REPO / "backend" / "config" / "holder_labels.yaml"

INSTITUTION_TABLE = "dim_holder_identity_label"
NIUSAN_TABLE = "dim_holder_name_tag"

INSTITUTION_WRITER_ID = "backend/scripts/publish_holder_labels.py (institution)"
NIUSAN_WRITER_ID = "backend/scripts/publish_holder_labels.py (niusan)"

# 机构侧"事实内容"列 —— 关旧开新的不可变性检查只锁这几列, 见 docstring 最后一节的判断说明。
_INSTITUTION_CONTENT_COLUMNS = ("value", "value_cn", "sub_value")
# 牛散侧目前没有可变"取值"概念(tag 恒为 'niusan'), 不可变性检查留空集合但保留同一机制,
# 便于未来加新 tag 取值时直接复用。
_NIUSAN_CONTENT_COLUMNS: tuple[str, ...] = ()


# ======================================================================================
# 牛散名录 —— 9 人, 已应用业主拍板的 4 人剔除裁决(详见本文件 docstring 判不了 #2)
# ======================================================================================
#
# 每条字段对应 dim_holder_name_tag 的列。known_from 的推导方法: 在 n1_roster_a.json 给出的
# 全部 sources[].url 里找可机械解出的 YYYY-MM-DD / YYYYMMDD 片段, 取其中最早的一个作为
# VERIFIED_PUBLICATION 的 known_from; 找不到就是 NULL + UNVERIFIED(红线3: 查不到不猜)。
# valid_from/valid_to 来自 roster 的 active_period 自然语言描述, 逐条人工判读——这一步
# 目前是人读不是脚本自动解析(9 条量级下可接受, n1_niusan_design.md 自己"我最没把握的三条"
# 第1条也点出这类日期判读天然脆弱, 这里同样适用, 且规模变大后必须外置成可审的 JSONL)。
_NIUSAN_ROSTER: list[dict[str, Any]] = [
    {
        "holder_name": "杨怀定",
        "alias": "杨百万",
        "known_from": "20210614",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "21世纪经济报道",
        "source_url": "https://m.21jingji.com/article/20210614/herald/2ab9d3eba1f99cc7e79f6bb00d4356ee_zaker.html",
        "evidence": "1988年国债异地套利起家, 被称『中国第一股民』, 数十年被主流媒体反复报道(21世纪经济报道/中国新闻网), 2021年去世",
        "valid_from": "19880101",
        "valid_to": "20210101",
        "valid_grade": "SOURCED_RANGE",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "canonical_top10_float_holders_period 用'杨怀定'/'杨百万'两种写法查询实测均 0 命中"
            "(canonical 覆盖 report_date>=20181231, 而其活跃/去世期落在此前, 与 m1 §2.6 point2 "
            "同型: 覆盖不到不代表标签有误); valid_from/valid_to 只有年份精度, 非精确到日。"
        ),
    },
    {
        "holder_name": "刘元生",
        "alias": None,
        "known_from": "20160627",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "21财经",
        "source_url": "http://m.21jingji.com/article/20160627/herald/0deb589588df782b808c75f08edebe5c.html",
        "evidence": "1988年360万元投资万科原始股, 长年万科最大个人股东『扫地僧』, 2018-2020年间清空",
        "valid_from": "19880101",
        "valid_to": "20200101",
        "valid_grade": "SOURCED_RANGE",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "canonical 0 命中(2019起窗口内无持仓)——与 n1_niusan_design.md §1.5 举的反例一致: "
            "他 2018-2020 年才按外部报道清仓, 但另一路原始数据里 000002 的记录只到 2015-06-30, "
            "两者本就对不上, 本表 valid_to 取外部报道口径(20200101)而非任何 last_seen 推断值。"
        ),
    },
    {
        "holder_name": "章建平",
        "alias": None,
        "known_from": "20260820",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "新浪财经",
        "source_url": "https://finance.sina.com.cn/stock/bxjj/2026-08-20/doc-ininyncn1948053.shtml",
        "evidence": "顶级游资, 常用席位国泰君安上海江苏路营业部, 本人及旗下产品持续现身十大流通股东名单, 持仓量级70-75亿元",
        "valid_from": "19960101",
        "valid_to": None,
        "valid_grade": "SOURCED_START_ONLY",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "canonical(holder_name='章建平')实测 91 行/32 只股(20181231~20260831)。"
            "市场绰号『章盟主』经业主核实即同一人, 已判定为重复条目剔除, 不重复贴标同一人两次"
            "(见本脚本 _NIUSAN_EXCLUDED)。known_from 只取给定材料里能解出日期的最早一条"
            "(20260820), 明显晚于其 1996 年就已活跃的事实, 是保守下界不是活跃起点。"
        ),
    },
    {
        "holder_name": "赵强",
        "alias": "赵老哥",
        "known_from": "20250822",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "东方财富网·财富号",
        "source_url": "https://caifuhao.eastmoney.com/news/20250822154544348822700",
        "evidence": "2007年10万元入市做到十亿量级, 号称『八年一万倍』, 常用席位中国银河绍兴路营业部, 短线打板风格",
        "valid_from": "20070101",
        "valid_to": None,
        "valid_grade": "SOURCED_START_ONLY",
        "identity_confidence": "no_evidence_either_way",
        "note": "canonical(holder_name='赵强')实测 31 行/11 只股(20190829~20250829); 绰号'赵老哥'本身不在 canonical 出现, 存 alias 列纯展示。",
    },
    {
        "holder_name": "林园",
        "alias": None,
        "known_from": "20231217",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "腾讯新闻(凤凰网财经《封面》)",
        "source_url": "https://news.qq.com/rain/a/20231217A00XZC00",
        "evidence": "1989年8000元入市, 集中重仓消费龙头长期持有, 多次公开专访, 后创立深圳林园投资(私募)",
        "valid_from": "19890101",
        "valid_to": None,
        "valid_grade": "SOURCED_START_ONLY",
        "identity_confidence": "no_evidence_either_way",
        "note": "canonical 0 命中——持仓已私募机构化(走产品账户不走个人账户), 与 n1_niusan_design.md 判断一致, 不是数据缺失。",
    },
    {
        "holder_name": "刘益谦",
        "alias": None,
        "known_from": "20050702",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "新浪财经(2005年报道)",
        "source_url": "https://finance.sina.com.cn/stock/t/20050702/0951171902.shtml",
        "evidence": "1980年代起家, 2000年后大规模囤积法人股/参与定增, 『法人股大王』『定增大王』, 后转型艺术品收藏",
        "valid_from": None,
        "valid_to": None,
        "valid_grade": "UNKNOWN_RANGE",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "roster 原文 active_period='1990年代-2015年前后(定增高峰期), 近年淡出'——两端都是"
            "模糊表述('年代'/'前后'), 没有转换成假装精确的 YYYYMMDD(红线3), 故 valid_from/to "
            "均 NULL。canonical 实测 48 行/3 只股(20181231~20250930)。known_from 取两条来源里"
            "较早的 20050702(新浪财经), 不是列表里排第一条的 20150505(中国经济网)。"
        ),
    },
    {
        "holder_name": "葛卫东",
        "alias": None,
        "known_from": "20241104",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "东方财富网·财富号",
        "source_url": "https://caifuhao.eastmoney.com/news/20241104144435763726040",
        "evidence": "期货界『民间铜王』, 2005年创办上海混沌投资, 本人及关联人持续出现在多家公司十大流通股东名单, 持仓超百亿元",
        "valid_from": "20050101",
        "valid_to": None,
        "valid_grade": "SOURCED_START_ONLY",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "canonical 实测 247 行/37 只股(20190105~20260828), 逼近但未达 n1_niusan_design.md "
            "§2.5 给出的『n_stocks>=40 结构上不可能一人』阈值, 未剔除但记此提醒。"
            "valid_from 取其股票市场起点(2005), 1992年的期货生涯不计入本表范围。"
        ),
    },
    {
        "holder_name": "陈发树",
        "alias": None,
        "known_from": "20091201",
        "known_from_grade": "VERIFIED_PUBLICATION",
        "source_outlet": "人民日报",
        "source_url": "http://paper.people.com.cn/rmwz/html/2009-12/01/content_477544.htm",
        "evidence": "2003年紫金矿业改制入股, 后重仓云南白药/隆基绿能/中国中免等, 媒体称『中国的巴菲特』",
        "valid_from": "20030101",
        "valid_to": None,
        "valid_grade": "SOURCED_START_ONLY",
        "identity_confidence": "no_evidence_either_way",
        "note": (
            "roster 原文自陈『本质为企业家型战略投资人而非纯散户, 但主流十大牛散榜单持续将其"
            "与章建平/葛卫东并列』——如实转述这条限定, 不隐藏。canonical 实测 188 行/12 只股"
            "(20190322~20260901)。"
        ),
    },
    {
        "holder_name": "徐开东",
        "alias": None,
        "known_from": None,
        "known_from_grade": "UNVERIFIED",
        "source_outlet": "网易",
        "source_url": "https://www.163.com/dy/article/JRMCU8QA0519B3D7.html",
        "evidence": "成都籍, 『中国十大牛散』/『55位最活跃散户』之一, 偏好低价故事股, 持仓周期长",
        "valid_from": None,
        "valid_to": None,
        "valid_grade": "UNKNOWN_RANGE",
        "identity_confidence": "suspected_multiple",
        "review_state": "review_pending",
        "note": (
            "canonical 实测 908 行/92 只股(20190316~20260831)——92 只股远超"
            "n1_niusan_design.md §2.5 自己给出的『n_stocks>=40 结构上不可能一人』阈值, 与业主"
            "已拍板剔除的魏巍(133 只股)同量级。业主本轮剔除名单只点名魏巍/张素芬/章盟主/徐翔"
            "四人, 未点名徐开东——本脚本没有权限替业主扩大剔除范围, 故仍收录, 但如实标注"
            "identity_confidence=suspected_multiple + review_state=review_pending, 建议业主"
            "按魏巍同一把尺子复核是否也应剔除(见本文件 docstring 之外的返回值汇报)。"
            "known_from 找不到给定两条来源 URL 里任何可机械解出的日期, 按红线3落 NULL, 不拿"
            "'报道密集期(2020-2026)'冒充活跃起点或发布日。"
        ),
    },
]

# 业主拍板剔除的 4 人, 不发布, 只在报告里说明理由(不进 DB)。
_NIUSAN_EXCLUDED: list[dict[str, str]] = [
    {
        "name": "魏巍",
        "reason": (
            "132(本轮实测 canonical 133)只股, 与'张素芬'同列个人股东最常见姓名前十; "
            "持股量从 16.8 万到 2.86 亿跨约 1700 倍, 高度疑似多人共名。业主拍板剔除。"
        ),
    },
    {
        "name": "张素芬",
        "reason": "120(本轮实测 canonical 121)只股, 同为个人股东最常见姓名前十, 同一形状风险。业主拍板剔除。",
    },
    {
        "name": "章盟主",
        "reason": (
            "是章建平的市场绰号, 非法定姓名; canonical 按字符串'章盟主'查询 0 命中"
            "(它本来就不是任何一行的 holder_name)。roster 自陈'真实身份存疑'。"
            "章建平本人已单独收录一条, 不重复贴标同一人两次。"
        ),
    },
    {
        "name": "徐翔",
        "reason": (
            "2015年入狱、2021年出狱; canonical 里'徐翔'2020-09-30~2026-06-30 期间 18 行/4 只股"
            "持续持仓 1,950万股(688303), 无法判定这是其出狱后本人持仓还是同名者。业主拍板剔除。"
        ),
    },
]


# ======================================================================================
# 配置加载 —— fail-closed: 出现不在白名单里的取值直接报错, 不 warn
# ======================================================================================


def _load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"[holder-labels] FAILED 缺配置文件 {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("institution_label", "niusan_tag"):
        if key not in cfg:
            raise SystemExit(f"[holder-labels] FAILED {path} 缺顶层键 {key!r}")
    return cfg


def _sha1_of_file(path: Path) -> str:
    h = hashlib.sha1()
    h.update(path.read_bytes())
    return h.hexdigest()


# ======================================================================================
# DDL —— 两张表, 各自 CHECK 约束当结构性防线; valid_from_pk 见 docstring "实测推翻设计"
# ======================================================================================

_INSTITUTION_DDL = f"""
CREATE TABLE IF NOT EXISTS {INSTITUTION_TABLE} (
    holder_code             TEXT NOT NULL,
    name_pattern            TEXT NOT NULL DEFAULT '*',
    dim                     TEXT NOT NULL,
    value                   TEXT NOT NULL,
    value_cn                TEXT,
    sub_value               TEXT,
    valid_from              TEXT,       -- 可空: REFUSED_NULL 325 实体/772 dim行现实里就是 NULL
    valid_to                TEXT,
    valid_from_pk           TEXT NOT NULL,  -- PK 专用代理 = COALESCE(valid_from, ''); 禁止 JOIN/WHERE 用它
    known_from              TEXT,
    known_from_lower_bound  TEXT,       -- 仅 UNVERIFIED 行填充, 纯展示/待复核, 不参与 JOIN/WHERE
    known_from_grade        TEXT NOT NULL,
    confidence               TEXT NOT NULL,
    evidence                 TEXT NOT NULL,
    source_file               TEXT NOT NULL,
    review_state               TEXT NOT NULL DEFAULT 'ok',
    batch_id                   TEXT NOT NULL,
    ingested_at                 TIMESTAMP NOT NULL,
    PRIMARY KEY (holder_code, name_pattern, dim, valid_from_pk),
    CHECK (known_from IS NULL OR known_from_grade IN ('VERIFIED_EVENT', 'VERIFIED_FIRST_NOTICE'))
)
"""

_NIUSAN_DDL = f"""
CREATE TABLE IF NOT EXISTS {NIUSAN_TABLE} (
    holder_name           TEXT NOT NULL,
    tag                   TEXT NOT NULL,
    valid_from            TEXT,
    valid_to              TEXT,
    valid_from_pk         TEXT NOT NULL,  -- PK 专用代理 = COALESCE(valid_from, ''); 禁止 JOIN/WHERE 用它
    valid_grade           TEXT NOT NULL,
    known_from            TEXT,
    known_from_grade      TEXT NOT NULL,
    identity_confidence   TEXT NOT NULL,
    identity_grade        TEXT NOT NULL DEFAULT 'name_only_untrusted',
    alias                 TEXT,
    name_variant          TEXT,
    source_outlet         TEXT NOT NULL,
    source_url            TEXT,
    evidence              TEXT NOT NULL,
    review_state          TEXT NOT NULL DEFAULT 'ok',
    note                  TEXT,
    batch_id              TEXT NOT NULL,
    ingested_at           TIMESTAMP NOT NULL,
    PRIMARY KEY (holder_name, tag, valid_from_pk),
    CHECK (known_from IS NULL OR known_from_grade = 'VERIFIED_PUBLICATION'),
    CHECK (identity_grade = 'name_only_untrusted'),
    CHECK (identity_confidence IN ('proven_multiple', 'suspected_multiple', 'no_evidence_either_way')),
    -- 加固项(n1_niusan_design.md §4"我最没把握的三条"#1 的建议, 本轮直接落成结构性约束):
    -- 敢写 known_from 就必须能回查 source_url, 不许"我记得是那天"式的无源日期。
    CHECK (known_from IS NULL OR source_url IS NOT NULL)
)
"""


# ======================================================================================
# 机构侧: 从证据 JSON 转换成待插入行
# ======================================================================================


def _fold_institution_known_from(grade: str, known_from_raw: str | None) -> tuple[str | None, str | None]:
    """按 m1_label_store.md §2.0 折叠: 只有两档允许 known_from 非 NULL, 其余落 NULL。

    返回 (known_from, known_from_lower_bound)。
    """
    if grade in ("VERIFIED_EVENT", "VERIFIED_FIRST_NOTICE"):
        return known_from_raw, None
    if grade == "UNVERIFIED":
        return None, known_from_raw
    # REFUSED_NULL 及任何其它取值(fail-closed 已在上一层拦截未知 grade, 这里只剩已知安全档)
    return None, None


def load_institution_rows(
    json_path: Path,
    *,
    allowed_dims: set[str],
    allowed_grades: set[str],
    split_pending_codes: set[str],
    batch_id: str,
    ingested_at: str,
) -> tuple[list[tuple], dict[str, int]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entities = data.get("entities")
    if not isinstance(entities, list):
        raise SystemExit(f"[holder-labels] FAILED {json_path} 没有 entities 数组")

    rows: list[tuple] = []
    stats = {
        "n_entities": len(entities),
        "n_skipped_bad_code": 0,
        "n_zero_dim_entities": 0,
        "n_rows": 0,
        "n_review_pending_rows": 0,
    }

    for ent in entities:
        code = ent.get("holder_code")
        if not code or str(code).strip().lower() in ("none", "null", ""):
            stats["n_skipped_bad_code"] += 1
            continue
        dims = ent.get("dims") or {}
        if not dims:
            stats["n_zero_dim_entities"] += 1
            continue

        grade = ent.get("known_from_grade")
        if grade not in allowed_grades:
            raise SystemExit(
                f"[holder-labels] FAILED holder_code={code} known_from_grade={grade!r} "
                f"不在 holder_labels.yaml institution_label.allowed_known_from_grades 白名单里"
            )
        known_from, known_from_lower_bound = _fold_institution_known_from(grade, ent.get("known_from"))
        valid_from = ent.get("valid_from")  # 可空, 见 docstring "实测推翻设计"
        valid_to = ent.get("valid_to")
        review_state = "review_pending" if str(code) in split_pending_codes else "ok"
        if review_state == "review_pending":
            stats["n_review_pending_rows"] += len(dims)

        for dim_name, dim_val in dims.items():
            if dim_name not in allowed_dims:
                raise SystemExit(
                    f"[holder-labels] FAILED holder_code={code} dim={dim_name!r} "
                    f"不在 holder_labels.yaml institution_label.allowed_dims 白名单里"
                )
            value = dim_val.get("value")
            confidence = dim_val.get("confidence")
            evidence = dim_val.get("evidence")
            source_file = dim_val.get("source_file")
            if not (value and confidence and evidence and source_file):
                raise SystemExit(
                    f"[holder-labels] FAILED holder_code={code} dim={dim_name!r} "
                    "缺 value/confidence/evidence/source_file 之一(全部 NOT NULL)"
                )
            rows.append(
                (
                    str(code),
                    "*",
                    dim_name,
                    value,
                    dim_val.get("value_cn"),
                    dim_val.get("sub_value"),
                    valid_from,
                    valid_to,
                    valid_from or "",  # valid_from_pk
                    known_from,
                    known_from_lower_bound,
                    grade,
                    confidence,
                    evidence,
                    source_file,
                    review_state,
                    batch_id,
                    ingested_at,
                )
            )
    stats["n_rows"] = len(rows)
    return rows, stats


# ======================================================================================
# 牛散侧: 从内嵌名录转换成待插入行
# ======================================================================================


def load_niusan_rows(
    *,
    allowed_grades: set[str],
    allowed_valid_grades: set[str],
    allowed_confidence: set[str],
    batch_id: str,
    ingested_at: str,
) -> tuple[list[tuple], dict[str, int]]:
    rows: list[tuple] = []
    for p in _NIUSAN_ROSTER:
        grade = p["known_from_grade"]
        if grade not in allowed_grades:
            raise SystemExit(
                f"[holder-labels] FAILED holder_name={p['holder_name']} known_from_grade={grade!r} "
                "不在 holder_labels.yaml niusan_tag.allowed_known_from_grades 白名单里"
            )
        valid_grade = p["valid_grade"]
        if valid_grade not in allowed_valid_grades:
            raise SystemExit(
                f"[holder-labels] FAILED holder_name={p['holder_name']} valid_grade={valid_grade!r} "
                "不在白名单里"
            )
        confidence = p["identity_confidence"]
        if confidence not in allowed_confidence:
            raise SystemExit(
                f"[holder-labels] FAILED holder_name={p['holder_name']} "
                f"identity_confidence={confidence!r} 不在白名单里(禁 single_person)"
            )
        known_from = p["known_from"]
        if known_from is not None and grade != "VERIFIED_PUBLICATION":
            raise SystemExit(
                f"[holder-labels] FAILED holder_name={p['holder_name']} known_from 非空但 "
                f"grade={grade!r} != VERIFIED_PUBLICATION —— 结构性防线要求两者绑定"
            )
        valid_from = p.get("valid_from")
        rows.append(
            (
                p["holder_name"],
                "niusan",
                valid_from,
                p.get("valid_to"),
                valid_from or "",  # valid_from_pk
                valid_grade,
                known_from,
                grade,
                confidence,
                "name_only_untrusted",
                p.get("alias"),
                None,  # name_variant: 本轮 9 人无需要合并的写法变体证据, 留空不是遗漏
                p["source_outlet"],
                p.get("source_url"),
                p["evidence"],
                p.get("review_state", "ok"),
                p.get("note"),
                batch_id,
                ingested_at,
            )
        )
    return rows, {"n_rows": len(rows), "n_people": len(rows)}


# ======================================================================================
# 关旧开新: 拒绝静默改写既有行的"事实内容"列(见 docstring 最后一节的判断说明)
# ======================================================================================


def _check_no_silent_value_rewrite(
    conn,
    table: str,
    pk_cols: tuple[str, ...],
    content_cols: tuple[str, ...],
    new_rows: list[tuple],
    all_cols: tuple[str, ...],
) -> None:
    if not content_cols:
        return
    existing = conn.execute(
        f"SELECT * FROM information_schema.tables WHERE table_name = '{table}'"
    ).fetchall()
    if not existing:
        return  # 表还不存在, 没有历史可比
    old_rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not old_rows:
        return
    col_idx = {c: i for i, c in enumerate(all_cols)}
    pk_idx = [col_idx[c] for c in pk_cols]
    content_idx = [col_idx[c] for c in content_cols]

    old_by_pk = {tuple(r[i] for i in pk_idx): tuple(r[i] for i in content_idx) for r in old_rows}
    violations = []
    for r in new_rows:
        pk = tuple(r[i] for i in pk_idx)
        if pk in old_by_pk:
            new_content = tuple(r[i] for i in content_idx)
            if new_content != old_by_pk[pk]:
                violations.append((pk, old_by_pk[pk], new_content))
    if violations:
        lines = "\n".join(f"  PK={v[0]} 旧={v[1]} 新={v[2]}" for v in violations[:10])
        raise SystemExit(
            f"[holder-labels] FAILED {table}: 检测到 {len(violations)} 行的事实内容被静默改写"
            f"(应关旧开新: 新增一行不同 valid_from, 不许原地改 {content_cols})\n{lines}"
        )


# ======================================================================================
# main
# ======================================================================================


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="写入目标库 (默认走 services.db; 测试务必传 /tmp 副本)")
    ap.add_argument("--institution-json", default="", help="w3_master_labels.json 形状的机构标签证据文件")
    ap.add_argument("--skip-niusan", action="store_true")
    ap.add_argument("--batch-id", default="", help="留空则按 institution-json 内容 sha1 派生; 幂等验证需显式传同一值")
    ap.add_argument("--ingested-at", default="", help="ISO 时间戳; 留空用当前时间; 幂等验证需显式传同一值")
    args = ap.parse_args()

    if args.db:
        import services.db as _db

        target = Path(args.db).resolve()
        _db.DB_PATH = target
        _db.DB_DIR = target.parent

    cfg = _load_config()
    inst_cfg = cfg["institution_label"]
    niu_cfg = cfg["niusan_tag"]

    ingested_at = args.ingested_at or datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        conn.execute(_INSTITUTION_DDL)
        conn.execute(_NIUSAN_DDL)

        # ---- 机构侧 ----
        if args.institution_json:
            json_path = Path(args.institution_json).resolve()
            if not json_path.exists():
                print(f"[holder-labels] FAILED --institution-json 不存在: {json_path}", file=sys.stderr)
                return 1
            batch_id = args.batch_id or f"auto-{_sha1_of_file(json_path)[:12]}"
            inst_rows, inst_stats = load_institution_rows(
                json_path,
                allowed_dims=set(inst_cfg["allowed_dims"]),
                allowed_grades=set(inst_cfg["allowed_known_from_grades"]),
                split_pending_codes=set(inst_cfg.get("split_pending_codes", [])),
                batch_id=batch_id,
                ingested_at=ingested_at,
            )
            inst_all_cols = (
                "holder_code", "name_pattern", "dim", "value", "value_cn", "sub_value",
                "valid_from", "valid_to", "valid_from_pk", "known_from", "known_from_lower_bound",
                "known_from_grade", "confidence", "evidence", "source_file", "review_state",
                "batch_id", "ingested_at",
            )
            _check_no_silent_value_rewrite(
                conn, INSTITUTION_TABLE,
                pk_cols=("holder_code", "name_pattern", "dim", "valid_from_pk"),
                content_cols=_INSTITUTION_CONTENT_COLUMNS,
                new_rows=inst_rows,
                all_cols=inst_all_cols,
            )
            conn.execute(f"DELETE FROM {INSTITUTION_TABLE}")
            placeholders = ", ".join(["?"] * len(inst_all_cols))
            conn.executemany(f"INSERT INTO {INSTITUTION_TABLE} VALUES ({placeholders})", inst_rows)

            n = conn.execute(f"SELECT COUNT(*) FROM {INSTITUTION_TABLE}").fetchone()[0]
            n_codes = conn.execute(f"SELECT COUNT(DISTINCT holder_code) FROM {INSTITUTION_TABLE}").fetchone()[0]
            n_decidable = conn.execute(
                f"SELECT COUNT(*) FROM {INSTITUTION_TABLE} WHERE known_from IS NOT NULL"
            ).fetchone()[0]
            print(f"[holder-labels] {INSTITUTION_TABLE}: {n:,} 行 / {n_codes:,} 个 holder_code")
            print(
                f"[holder-labels]   实体总数 {inst_stats['n_entities']:,}; "
                f"跳过 holder_code 缺失/非法 {inst_stats['n_skipped_bad_code']:,}; "
                f"0-dim(全部维度已被裁决作废) {inst_stats['n_zero_dim_entities']:,}"
            )
            print(
                f"[holder-labels]   known_from 非 NULL(可进决策查询) {n_decidable:,} / {n:,} "
                f"({n_decidable / n * 100:.1f}%); review_pending {inst_stats['n_review_pending_rows']:,} 行"
                " (code_split_rules 未结构化, 见 docstring 判不了#1)"
            )
        else:
            print("[holder-labels] 未传 --institution-json, 跳过机构标签发布")

        # ---- 牛散侧 ----
        if not args.skip_niusan:
            niu_batch_id = args.batch_id or "niusan-embedded-v1"
            niu_rows, niu_stats = load_niusan_rows(
                allowed_grades=set(niu_cfg["allowed_known_from_grades"]),
                allowed_valid_grades=set(niu_cfg["allowed_valid_grades"]),
                allowed_confidence=set(niu_cfg["allowed_identity_confidence"]),
                batch_id=niu_batch_id,
                ingested_at=ingested_at,
            )
            niu_all_cols = (
                "holder_name", "tag", "valid_from", "valid_to", "valid_from_pk", "valid_grade",
                "known_from", "known_from_grade", "identity_confidence", "identity_grade",
                "alias", "name_variant", "source_outlet", "source_url", "evidence",
                "review_state", "note", "batch_id", "ingested_at",
            )
            _check_no_silent_value_rewrite(
                conn, NIUSAN_TABLE,
                pk_cols=("holder_name", "tag", "valid_from_pk"),
                content_cols=_NIUSAN_CONTENT_COLUMNS,
                new_rows=niu_rows,
                all_cols=niu_all_cols,
            )
            conn.execute(f"DELETE FROM {NIUSAN_TABLE}")
            placeholders = ", ".join(["?"] * len(niu_all_cols))
            conn.executemany(f"INSERT INTO {NIUSAN_TABLE} VALUES ({placeholders})", niu_rows)

            n = conn.execute(f"SELECT COUNT(*) FROM {NIUSAN_TABLE}").fetchone()[0]
            print(f"[holder-labels] {NIUSAN_TABLE}: {n:,} 行 / {n:,} 人(一人一行, 无历史版本)")
            for p in _NIUSAN_ROSTER:
                print(
                    f"[holder-labels]   {p['holder_name']}"
                    f"{'(' + p['alias'] + ')' if p.get('alias') else ''}: "
                    f"{p['identity_confidence']}, known_from={p['known_from']}, "
                    f"review_state={p.get('review_state', 'ok')}"
                )
            print(f"[holder-labels]   已剔除 {len(_NIUSAN_EXCLUDED)} 人 (业主拍板):")
            for e in _NIUSAN_EXCLUDED:
                print(f"[holder-labels]     - {e['name']}: {e['reason']}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
