"""check_rule_compliance 单测 (2026-09-04, 门此前无测试)。

背景: 门原有 8 类 pattern, 其中 4 类 (Rule 6 magic alpha weight / sigma multiplier /
multiplier / threshold) 属于已退役的「量化/参数寻优」范式 —— 检查的是策略参数有没有
backtest/optuna evidence; 项目转成「策略验证」范式后系统不再产出/拟合这些参数, 4 条
连同其 rationale 一并从 check_rule_compliance.py 删除。本文件只测保留下来的 4 条:

  1. DB boundary raw duckdb.connect
  2. DB boundary hardcoded duckdb path
  3. Rule 5 silent except: pass  (不支持 evidence 豁免, 只能改写)
  4. Rule 7 hardcoded date / hardcoded stock_code (支持同行/上一行 evidence 豁免)

外加一条钉住 `# evidence:` 豁免只在同行/紧邻上一行 (纯注释行) 生效, 不是整个文件豁免
——这个项目在 doc_governance / doc_drift 上因为豁免作用域比意图大踩过三次坑
(feedback-warn-only-degrades-to-warn-nothing.md), check_rule_compliance 也该有测试钉住。

不断言宿主 git 状态: 全部经 monkeypatch `get_staged_diff()` 注入合成 diff, 不 stage
任何真实文件、不碰真实 git index —— 门本身用真实 (临时) staged diff 的反向验证走
CLAUDE.md 要求的手工验收步骤, 不放进本测试文件。
"""
from __future__ import annotations

import pytest

from scripts import check_rule_compliance as gate


def _run(monkeypatch, capsys, lines_by_file: dict[str, list[str]]) -> tuple[int, str]:
    """把 {path: [added_line, ...]} 伪装成 get_staged_diff() 的返回值并跑 main()。"""
    diffs = [
        (path, [(i + 1, line) for i, line in enumerate(lines)])
        for path, lines in lines_by_file.items()
    ]
    monkeypatch.setattr(gate, "get_staged_diff", lambda: diffs)
    code = gate.main()
    return code, capsys.readouterr().err


# ---------------------------------------------------------------------------
# 0. Rule 6 确认已删 (防回归: 别让量化 magic-number 检查悄悄回来)
# ---------------------------------------------------------------------------


def test_rule6_patterns_are_gone():
    names = [name for name, _ in gate.PATTERNS]
    assert names == [
        "Rule 5 silent except pass",
        "Rule 7 hardcoded date",
        "Rule 7 hardcoded stock_code",
    ]
    assert not any("Rule 6" in name for name in names)


# ---------------------------------------------------------------------------
# 1. DB boundary raw duckdb.connect
# ---------------------------------------------------------------------------


def test_raw_duckdb_connect_flagged(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {"backend/services/fake_reader.py": ["conn = duckdb.connect(resolved_path)"]},
    )
    assert code == 1
    assert "DB boundary raw duckdb.connect" in err


def test_raw_duckdb_connect_with_evidence_comment_passes(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_reader.py": [
                "conn = duckdb.connect(resolved_path)  "
                "# rule-compliance: ok evidence=one-off migration script"
            ]
        },
    )
    assert code == 0
    assert err == ""


# ---------------------------------------------------------------------------
# 2. DB boundary hardcoded duckdb path
# ---------------------------------------------------------------------------


def test_hardcoded_duckdb_path_flagged(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {"backend/services/fake_reader.py": ['DB_PATH = "data/scratch_extra.duckdb"']},
    )
    assert code == 1
    assert "DB boundary hardcoded duckdb path" in err


def test_hardcoded_duckdb_path_with_evidence_comment_passes(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_reader.py": [
                "# evidence: one-off migration, path retired after backfill",
                'DB_PATH = "data/scratch_extra.duckdb"',
            ]
        },
    )
    assert code == 0
    assert err == ""


def test_duckdb_path_via_manifest_lookup_passes(monkeypatch, capsys):
    # 不写字面量路径, 走 manifest 解析 -> 完全不触发检测
    code, err = _run(
        monkeypatch,
        capsys,
        {"backend/services/fake_reader.py": ["DB_PATH = get_database_manifest().path_for('foo')"]},
    )
    assert code == 0
    assert err == ""


# ---------------------------------------------------------------------------
# 3. Rule 5 silent except: pass — 不支持 evidence 豁免
# ---------------------------------------------------------------------------


def test_bare_except_pass_flagged(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_worker.py": [
                "try:",
                "    risky()",
                "except Exception:",
                "    pass",
            ]
        },
    )
    assert code == 1
    assert "Rule 5 silent except pass" in err


def test_except_with_real_handling_passes(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_worker.py": [
                "try:",
                "    risky()",
                "except Exception:",
                "    log.warning('risky() failed')",
            ]
        },
    )
    assert code == 0
    assert err == ""


def test_bare_except_pass_evidence_comment_does_not_exempt(monkeypatch, capsys):
    # 3 号规则明确不支持 evidence 豁免 —— 就算 pass 行本身带 evidence 注释也还是要红,
    # 因为 main() 对 "silent except" 分支从不调用 has_evidence()。
    # (注意: evidence 注释不能加在 "except Exception:" 那一行本身 —— 该行的正则
    # `:\s*$` 要求冒号后只能是空白, 尾随注释会让整条正则连"是不是 bare except"都
    # 判不出来, 不是本测试想测的东西, 所以注释放在 pass 行上。)
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_worker.py": [
                "try:",
                "    risky()",
                "except Exception:",
                "    pass  # evidence: reviewed, intentional",
            ]
        },
    )
    assert code == 1
    assert "Rule 5 silent except pass" in err


# ---------------------------------------------------------------------------
# 4. Rule 7 hardcoded date / hardcoded stock_code
# ---------------------------------------------------------------------------


def test_hardcoded_date_flagged(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {"backend/services/fake_dates.py": ['START_DATE = "2024-01-01"']},
    )
    assert code == 1
    assert "Rule 7 hardcoded date" in err


def test_hardcoded_date_with_evidence_comment_passes(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_dates.py": [
                'START_DATE = "2024-01-01"  # evidence: backtest commit abc1234'
            ]
        },
    )
    assert code == 0
    assert err == ""


def test_hardcoded_stock_code_flagged(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {"backend/services/fake_codes.py": ['TARGET = "600000"']},
    )
    assert code == 1
    assert "Rule 7 hardcoded stock_code" in err


def test_hardcoded_stock_code_with_evidence_comment_passes(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_codes.py": [
                'TARGET = "600000"  # from yaml: watchlist_defaults'
            ]
        },
    )
    assert code == 0
    assert err == ""


def test_hardcoded_date_via_calendar_call_passes(monkeypatch, capsys):
    # 合规写法: 不写字面量, 从 services.calendar 一手取
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_dates.py": [
                "START_DATE = services.calendar.latest_closed_or_raise()"
            ]
        },
    )
    assert code == 0
    assert err == ""


# ---------------------------------------------------------------------------
# 5. evidence 豁免只在同行/紧邻上一行(纯注释行)生效, 不是整个文件豁免
# ---------------------------------------------------------------------------


def test_evidence_on_immediate_prev_comment_line_exempts(monkeypatch, capsys):
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_dates.py": [
                "# evidence: backtest commit abc123",
                'START_DATE = "2024-01-01"',
            ]
        },
    )
    assert code == 0
    assert err == ""


def test_evidence_elsewhere_in_file_does_not_exempt_non_adjacent_violation(monkeypatch, capsys):
    # 同一个 evidence 注释存在于文件里, 但既不在违规行同行也不紧邻其上一行
    # (中间隔了一行非注释代码) —— 必须仍然报违规, 证明豁免作用域是逐行的,
    # 不是「文件里出现过 evidence 关键词就整份豁免」。
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_dates.py": [
                "# evidence: backtest commit abc123",
                "OTHER = 1",
                'START_DATE = "2024-01-01"',
            ]
        },
    )
    assert code == 1
    assert "Rule 7 hardcoded date" in err


def test_evidence_comment_on_different_file_does_not_exempt_other_file(monkeypatch, capsys):
    # 同一次 commit 里另一个文件的 evidence 注释不该跨文件豁免。
    code, err = _run(
        monkeypatch,
        capsys,
        {
            "backend/services/fake_evidenced.py": [
                'OTHER_DATE = "2024-01-01"  # evidence: backtest commit abc123'
            ],
            "backend/services/fake_dates.py": ['START_DATE = "2024-01-01"'],
        },
    )
    assert code == 1
    assert err.count("Rule 7 hardcoded date") == 1
    assert "fake_dates.py" in err
