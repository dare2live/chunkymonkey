"""check_dead_references D 扫 (module= 字面量) 单测 — 2026-06-28 残留清理批0 流程根治。

根因 (三轮残留审计坐实): B 扫只抓 from/import 语句, 抓不到 ClientSpec dataclass 的
module="services.X"/"scripts.X" 字符串字面量 → 14 条死 ClientSpec(module=scripts.<已删>)
系统性逃逸门, 死登记反复积累。D 扫补这个盲区。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_dead_references as cdr  # noqa: E402


def test_scan_d_regex_matches_module_literal():
    """D 扫正则抓 module="services.X"/"scripts.X" 字面量 (= 和 : 两种 + 单双引号)。"""
    assert cdr._MODULE_LITERAL_RE.search('module="scripts.run_daily_topk",').group(1) == "scripts.run_daily_topk"
    assert cdr._MODULE_LITERAL_RE.search("module='services.xdxr_client'").group(1) == "services.xdxr_client"
    assert cdr._MODULE_LITERAL_RE.search('  module: "routers.foo"').group(1) == "routers.foo"
    # 非 services/scripts/routers 前缀不匹配 (避免误伤业务字符串)
    assert cdr._MODULE_LITERAL_RE.search('module="numpy.random"') is None
    # 无引号 (yaml 裸值) 不匹配 — D 扫只管 python dataclass module="..." (有引号), yaml 归 C 扫
    assert cdr._MODULE_LITERAL_RE.search("module: routers.foo") is None


def test_scan_d_no_dead_module_literal_in_repo():
    """删完 14 死 ClientSpec 后 scan_d 应 0 (门脚本自排除 + clients_registry 无死 module=)。
    防回归: 未来若有人加 module=scripts.<不存在> 的死 ClientSpec, 此测试 + CI gate 会红。"""
    fails = cdr.scan_d_dead_module_literal()
    assert fails == [], f"scan_d 应 0 死 module= 字面量, 实得 {len(fails)}: {fails[:5]}"


def test_scan_e_regex_matches_sql_table_ref():
    """E 扫正则抓 SQL FROM/JOIN 后跟项目命名惯例 (fact_/mart_/dim_/raw_/stg_) 的表名。"""
    assert cdr._SQL_TABLE_REF_RE.search('SELECT * FROM mart_p0b_lambdamart_v6_predictions').group(1) == "mart_p0b_lambdamart_v6_predictions"
    assert cdr._SQL_TABLE_REF_RE.search('JOIN "raw_tushare_daily" t ON ...').group(1) == "raw_tushare_daily"
    assert cdr._SQL_TABLE_REF_RE.search("from dim_active_a_stock").group(1) == "dim_active_a_stock"
    # 动态 f-string 表名 (FROM {table}) 不匹配字面前缀, 静态无法核实故跳过不误判
    assert cdr._SQL_TABLE_REF_RE.search('FROM {table} t WHERE ...') is None
    # 非项目命名惯例前缀 (如临时视图/CTE别名) 不匹配, 避免误伤
    assert cdr._SQL_TABLE_REF_RE.search('FROM information_schema.tables') is None
    # 裸前缀本身(2026-07-06 实测反例): 文档字符串里"禁止 FROM raw_*"这类规则说明不能被
    # 误判成真表名——真表名从不会只是裸前缀, 前缀后至少要有 1 个字符
    assert cdr._SQL_TABLE_REF_RE.search('禁内联 FROM raw_* (check_serve_read_layer D1)') is None


def test_scan_e_skips_not_fails_when_db_locked(monkeypatch):
    """库被并发写锁占用打不开时, E 扫必须跳过 (返回空) 而非把锁定误判成表不存在.
    2026-07-06 实测反例: rebuild_all() 跑批期间跑本门, smartmoney.duckdb 打不开曾导致
    64 处假阳性 (该库里的全部活表被误判死引用)。"""
    monkeypatch.setattr(cdr, "_live_table_names", lambda: (set(), False))
    assert cdr.scan_e_sql_table_refs() == []


def test_live_table_names_no_db_files_is_not_reachable(monkeypatch, tmp_path):
    """一个 .duckdb 文件都不存在时 (CI 全新 checkout, data/*.duckdb 是 gitignored 生产数据从不
    进 git), all_reachable 必须是 False, 不能停在初值误判成"查了0个库全部通过"。
    2026-07-06 实测反例: CI 无任何 .duckdb 文件 → glob 空列表 → 循环体从不执行 → 旧实现
    all_reachable 停在初值 True → 全仓库 83 处正常 SQL 表引用被误判死引用, 击穿 CI。"""
    monkeypatch.setattr(cdr, "DATA_DIR", tmp_path)  # 空目录, 无 .duckdb 文件
    live, all_reachable = cdr._live_table_names()
    assert live == set()
    assert all_reachable is False, "0 个库文件时必须视为'不可信', 不能当成'确认查过'"


def test_scan_e_skips_not_fails_when_no_db_files_exist(monkeypatch, tmp_path):
    """端到端: DATA_DIR 空 (CI 场景) 时 scan_e 整体跳过 (返回空), 不产生假阳性。"""
    monkeypatch.setattr(cdr, "DATA_DIR", tmp_path)
    assert cdr.scan_e_sql_table_refs() == []


def test_known_safe_list_entries_still_match_reality():
    """白名单每条必须仍然成立: (1) 引用方文件仍存在且仍引用该表名 (2) 该表仍确实不存在
    (若表后来被重建, 条目该删——白名单不能变成"曾经安全, 现在盲区")。"""
    live, all_reachable = cdr._live_table_names()
    assert all_reachable, "本测试需要真库可达才能验证白名单仍然成立"
    for (rel, tbl), _reason in cdr._SQL_TABLE_REF_KNOWN_SAFE.items():
        p = cdr.BACKEND / rel
        assert p.exists(), f"白名单条目引用的文件 {rel} 已不存在, 该条目该删"
        text = p.read_text(encoding="utf-8", errors="replace")
        assert tbl in text, f"白名单条目 {rel} 已不再引用 {tbl}, 该条目该删"
        assert tbl not in live, f"表 {tbl} 现在已存在 (可能被重建), 白名单条目该删让 E 扫重新覆盖它"


def test_full_dead_references_gate_passes():
    """整门 (A import-services + B dead-services-ref + C config-dead-path + D module-literal
    + E sql-table-ref) 全绿。"""
    assert cdr.main() == 0
