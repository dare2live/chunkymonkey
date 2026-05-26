#!/usr/bin/env python3
"""Session handoff 完整性审计 — session 结束前强制跑, 不通过不收工.

扫描当次 session 的 git commits, 提取关键主题/数据/决策,
跟 goal.md + handoff.md 对比, 列出未覆盖的遗漏.

Usage:
    PYTHONPATH=backend python scripts/session_handoff_audit.py
    PYTHONPATH=backend python scripts/session_handoff_audit.py --since "2026-05-26"
    PYTHONPATH=backend python scripts/session_handoff_audit.py --handoff analysis/session_handoff_20260526.md
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _git_commits_since(since: str) -> list[dict]:
    """获取 since 日期以来的所有 commits."""
    result = subprocess.run(
        ["git", "log", f"--since={since}", "--format=%H|%s|%an|%ai", "--no-merges"],
        capture_output=True, text=True,
    )
    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) >= 2:
            commits.append({"hash": parts[0][:8], "subject": parts[1], "author": parts[2] if len(parts) > 2 else "", "date": parts[3] if len(parts) > 3 else ""})
    return commits


def _git_diff_files(since: str) -> list[str]:
    """获取 since 以来改动的文件."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"HEAD@{{{since}}}..HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--name-only", "--format="],
            capture_output=True, text=True,
        )
    return list(set(f.strip() for f in result.stdout.strip().split("\n") if f.strip()))


def _extract_topics_from_commits(commits: list[dict]) -> list[str]:
    """从 commit messages 提取关键主题."""
    topics = set()
    keywords_map = {
        "formula": "公式改动",
        "preflight": "审计工具",
        "plan_validator": "计划验证",
        "data_audit": "数据审计",
        "leakage": "leakage 修复",
        "GCP": "GCP 跑批",
        "grill": "Grill gate",
        "walk.forward": "Walk-forward",
        "search.space": "Search space",
        "bank": "Bank 公式",
        "smartmoney|adapter": "SmartMoney 数据",
        "profiler|ranker|pool": "四层架构",
        "300616|wave|doji": "300616 策略",
        "skill": "Skills 安装",
        "sync|update": "数据同步",
        "bug|fix": "Bug 修复",
        "board|板块": "板块适配",
        "Codex": "Codex 协作",
        "front|前端|UI": "前端改动",
        "cost|成本|tx_cost": "交易成本",
    }
    for commit in commits:
        subject = commit["subject"]
        for pattern, topic in keywords_map.items():
            if re.search(pattern, subject, re.IGNORECASE):
                topics.add(topic)
    return sorted(topics)


def _extract_numbers_from_commits(commits: list[dict]) -> list[str]:
    """从 commit messages 提取关键数字 (score/win_rate/count/etc)."""
    numbers = []
    for commit in commits:
        subject = commit["subject"]
        found = re.findall(r'\d+[./]\d+|score[=:]\s*[\d.]+|win[=:]\s*[\d.%]+|\d+\s*(?:公式|stocks|PASS|FAIL|rows|signals)', subject)
        for f in found:
            numbers.append(f"{commit['hash']}: {f}")
    return numbers


def _check_coverage(topics: list[str], doc_text: str) -> list[str]:
    """检查哪些主题在文档中没覆盖."""
    topic_keywords = {
        "公式改动": ["formula", "公式"],
        "审计工具": ["preflight", "审计", "audit"],
        "计划验证": ["plan_validator", "计划验证"],
        "数据审计": ["data_audit", "数据审计", "完整性"],
        "leakage 修复": ["leakage", "未来函数", "PIT"],
        "GCP 跑批": ["GCP", "跑批", "batch"],
        "Grill gate": ["grill", "拷问"],
        "Walk-forward": ["walk-forward", "walk_forward", "70/30"],
        "Search space": ["search space", "search_space", "参数搜索"],
        "Bank 公式": ["bank", "49", "KEEP", "REWORK", "DROP"],
        "SmartMoney 数据": ["smartmoney", "adapter", "SmartMoney"],
        "四层架构": ["profiler", "ranker", "pool", "四层"],
        "300616 策略": ["300616", "三波", "wave"],
        "Skills 安装": ["skill", "grill-with-docs", "diagnose"],
        "数据同步": ["sync", "更新", "dim_stock"],
        "Bug 修复": ["bug", "fix", "修复"],
        "板块适配": ["板块", "board", "创业板"],
        "Codex 协作": ["Codex", "codex"],
        "前端改动": ["前端", "formula-view", "UI"],
        "交易成本": ["VWAP", "tx_cost", "交易成本", "10.4"],
    }
    missing = []
    for topic in topics:
        keywords = topic_keywords.get(topic, [topic])
        found = any(kw.lower() in doc_text.lower() for kw in keywords)
        if not found:
            missing.append(topic)
    return missing


def _check_new_files_documented(changed_files: list[str], doc_text: str) -> list[str]:
    """检查新建的 Python 文件是否在文档中提到."""
    undocumented = []
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        basename = Path(f).stem
        filename = Path(f).name
        if basename.startswith("__") or basename.startswith("test_"):
            continue
        if basename not in doc_text and filename not in doc_text and f not in doc_text:
            undocumented.append(f)
    return undocumented


def main() -> int:
    parser = argparse.ArgumentParser(description="Session handoff completeness audit")
    parser.add_argument("--since", default=None, help="Audit commits since date (default: today)")
    parser.add_argument("--handoff", default=None, help="Handoff file to check against")
    parser.add_argument("--goal", default="goal.md", help="Goal file to check against")
    args = parser.parse_args()

    since = args.since or datetime.now().strftime("%Y-%m-%d")

    print(f"=== Session Handoff Audit (since {since}) ===\n")

    # 1. Gather commits
    commits = _git_commits_since(since)
    print(f"[1] Commits: {len(commits)}")
    if not commits:
        print("  无 commits, 跳过审计")
        return 0

    # 2. Extract topics
    topics = _extract_topics_from_commits(commits)
    print(f"[2] 提取主题: {len(topics)}")
    for t in topics:
        print(f"  - {t}")

    # 3. Extract key numbers
    numbers = _extract_numbers_from_commits(commits)
    print(f"\n[3] 关键数字: {len(numbers)}")
    for n in numbers[:10]:
        print(f"  {n}")
    if len(numbers) > 10:
        print(f"  ... +{len(numbers)-10} more")

    # 4. Load documentation
    doc_text = ""
    goal_path = Path(args.goal)
    if goal_path.exists():
        doc_text += goal_path.read_text()
    if args.handoff:
        handoff_path = Path(args.handoff)
        if handoff_path.exists():
            doc_text += handoff_path.read_text()
    else:
        # 自动找最新 handoff
        handoffs = sorted(Path("analysis").glob("session_handoff_*.md"))
        if handoffs:
            doc_text += handoffs[-1].read_text()
            print(f"\n[4] 文档: {args.goal} + {handoffs[-1]}")
        else:
            print(f"\n[4] 文档: {args.goal} (无 handoff)")

    if not doc_text:
        print("  无文档可检查")
        return 1

    # 5. Check topic coverage
    missing_topics = _check_coverage(topics, doc_text)
    print(f"\n[5] 主题覆盖: {len(topics) - len(missing_topics)}/{len(topics)}")
    if missing_topics:
        print(f"  未覆盖:")
        for m in missing_topics:
            print(f"    MISS: {m}")

    # 6. Check new files documented
    changed_files = _git_diff_files(since)
    new_py = [f for f in changed_files if f.endswith(".py")]
    undocumented = _check_new_files_documented(new_py, doc_text)
    print(f"\n[6] 新/改 Python 文件: {len(new_py)}, 未在文档中提及: {len(undocumented)}")
    for f in undocumented[:5]:
        print(f"    MISS: {f}")

    # 7. Summary
    total_issues = len(missing_topics) + len(undocumented)
    print(f"\n{'='*50}")
    print(f"总计: {len(topics)} 主题, {len(missing_topics)} 未覆盖, {len(undocumented)} 文件未提及")

    # 7. 生成人工确认 checklist
    print(f"\n[7] Handoff 人工确认 checklist (工具无法自动判断的):")
    checklist = [
        "每个未完成任务有具体的 next step (不只是标题)?",
        "关键数字都记录了 (score / 行数 / 成本 / 日期)?",
        "失败的尝试记录了原因 (不只是'不 work')?",
        "用户的原话/指令有记录 (特别是方向性决策)?",
        "下个 session 的 Claude 读了 handoff 能不问用户就接着干?",
    ]
    for i, item in enumerate(checklist, 1):
        print(f"  [ ] {i}. {item}")

    if total_issues > 0:
        print(f"\nHANDOFF INCOMPLETE — 补完再收工 ({len(missing_topics)} 主题 + {len(undocumented)} 文件)")
        return 1
    else:
        print(f"\nHANDOFF AUTO-CHECK PASS — 人工确认上述 checklist 后可收工")
        return 0


if __name__ == "__main__":
    sys.exit(main())
