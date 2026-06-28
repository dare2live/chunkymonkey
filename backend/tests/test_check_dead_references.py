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


def test_full_dead_references_gate_passes():
    """整门 (A import-services + B dead-services-ref + C config-dead-path + D module-literal) 全绿。"""
    assert cdr.main() == 0
