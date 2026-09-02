#!/usr/bin/env python3
"""check_frozen_stamp_compare — 冻结落地戳不得与活契约/指针比相等 (2026-09-02).

# 它要防的病 (2026-09-02 两处活体故障, 同一根因):
#   ingest_batch.{contract_hash, config_hash, source_name} 是**落地那一刻的证据封印**
#   (payload_hash 把它们连同逐行签名一起 sha256), 按设计停在旧值;
#   accepted_partition / canonical_* 的戳是"这批数据符合哪个契约", 契约指纹算法一变
#   (2c4af4a08 把 source/api 移出 config_hash) 就跟着当前契约重打。
#   于是"批次戳 == 指针戳 / 批次戳 == 现算契约戳"这条等式在重打之后**必然为假**,
#   却仍散落在 N 处运行时代码里被当不变量断言:
#     - calendar_reader._load_and_verify_batch   → open_calendar_truth 永久 BLOCKED
#     - calendar_acceptance._validate_batch_identity_and_time → CONTRACT_DRIFT
#     - margin_acceptance.prove_current_landed_margin_batch → 20260828 追赶被"stale checkpoint"卡死
#   同型: source_name 是传输轴 (哪根水管), 与 contract.source 比相等 = 换源那天必炸。
#
# 判据 (一句话): **算法派生的指纹 + 传输轴标签是冻结证据, 只能用来重算封印, 不能与任何
# "现在"的值比相等**; 可比的是**人声明的身份**: contract_version / writer_id / dataset_id /
# partition_value, 以及内容 (canonical_hash / row_count) 与时间链。
#
# 本门抓的三种**写法形状** (全部来自今天真实代码, 不是想象的):
#   S1 direct : str(batch["config_hash"]) != contract.config_hash
#               batch["contract_hash"] != contract_hash          (handoff 形参)
#               batch["source_name"] != domain.source
#   S2 tuple  : actual = (str(batch["contract_hash"]), ...); expected = (contract.contract_hash, ...);
#               if actual != expected   (同一函数内经名字流过一次的元组)
#   S3 sql    : ... FROM ingest_batch ib ... WHERE ib.contract_hash = ?  (拿库里冻结列与参数比)
#
# 已知盲区 (诚实列出, 不假装它抓得全; 这些由行为测试兜底 —
#   tests/services/test_calendar_frozen_landing_stamp.py / test_margin_frozen_landing_stamp.py /
#   test_accept_frozen_landing_stamp.py 各自造"批次戳陈旧但指针/契约一致"的 fixture 跑真路径):
#   B1 经 dict 键循环间接比较 (calendar_reader 旧写法 exact = {...}; for name in exact: batch[name] != value)
#   B2 经下标索引而非键名取列 (row[2] / tuple(str(row[i]) for i in range(1, 6)))
#   B3 payload 封印重算时把**现算契约**当身份传进去 (calendar_acceptance 旧写法), 这是语义错不是比较形状
#   B4 跨函数传递后再比较 (形参名不含 batch/landed/pointer 语义)
#
# 不在判据内 (不报): contract_version / writer_id / dataset_id / partition_value 的比较 (声明身份);
#   pointer vs contract (accepted_partition 重打过, 必须一致); contract vs fresh (自洽);
#   research 快照绑定 (另一套子系统)。
#
# 用法:
#   PYTHONPATH=backend python backend/scripts/check_frozen_stamp_compare.py [--root backend/services] [--json]
# 退出码: 0 = 无发现; 1 = 有发现。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO / "backend" / "services"

# 冻结列: 只允许重算封印, 不允许与"现在"比相等。writer_id 刻意不在此列 (声明身份)。
FROZEN_KEYS = frozenset({"contract_hash", "config_hash", "source_name"})
# 活契约/域对象上的对应属性 (contract.contract_hash / domain.source / fresh.config_hash ...)。
LIVE_ATTRS = frozenset({"contract_hash", "config_hash", "source"})
# handoff 形参形状: accept_xxx(conn, batch_id, *, contract_hash: str, config_hash: str)。
LIVE_NAME_IDS = frozenset({"contract_hash", "config_hash"})
# 指针行的键 (accepted_partition 已重打, 与批次冻结戳比相等 = 病)。
POINTER_KEYS = frozenset({"contract_hash", "config_hash"})

# 变量名语义: 从 ingest_batch 读出来的行通常叫 batch / landed / checkpoint / ib。
FROZEN_BASE_RE = re.compile(r"batch|landed|landing|ingest|checkpoint|^ib$", re.IGNORECASE)
POINTER_BASE_RE = re.compile(r"pointer|accepted|^ap$", re.IGNORECASE)

_SQL_INGEST_RE = re.compile(r"(?:\bingest_batch\b|\{INGEST_BATCH_TABLE\})\s+(?:AS\s+)?(\w+)", re.IGNORECASE)
_SQL_INGEST_ANY_RE = re.compile(r"\bingest_batch\b|\{INGEST_BATCH_TABLE\}", re.IGNORECASE)
_SQL_FROM_TABLES_RE = re.compile(r"\b(?:FROM|JOIN)\s+(\{?\w+\}?)", re.IGNORECASE)
_SQL_MUTATION_RE = re.compile(r"^\s*(?:UPDATE|INSERT|DELETE)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # direct_compare | tuple_compare | sql_compare
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.kind}] {self.detail}"


# ---------------------------------------------------------------------------
# AST 形状识别
# ---------------------------------------------------------------------------


def _unwrap(node: ast.AST) -> ast.AST:
    """str(x) / int(x) 这类纯转换不改变语义, 剥掉看里面。"""
    while (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"str", "int"}
        and len(node.args) == 1
    ):
        node = node.args[0]
    return node


def _base_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _const_key(node: ast.Subscript) -> str | None:
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _is_frozen_read(node: ast.AST) -> bool:
    node = _unwrap(node)
    if not isinstance(node, ast.Subscript):
        return False
    key = _const_key(node)
    base = _base_name(node.value)
    return key in FROZEN_KEYS and base is not None and bool(FROZEN_BASE_RE.search(base))


def _is_live_read(node: ast.AST) -> bool:
    node = _unwrap(node)
    if isinstance(node, ast.Attribute):
        base = _base_name(node.value)
        # batch.source 这种是落地输入对象自己的属性, 不是活契约
        if base is not None and FROZEN_BASE_RE.search(base):
            return False
        return node.attr in LIVE_ATTRS
    if isinstance(node, ast.Name):
        return node.id in LIVE_NAME_IDS
    if isinstance(node, ast.Subscript):
        key = _const_key(node)
        base = _base_name(node.value)
        return key in POINTER_KEYS and base is not None and bool(POINTER_BASE_RE.search(base))
    return False


def _contains(node: ast.AST, predicate) -> bool:
    return any(predicate(child) for child in ast.walk(node))


def _tuple_flags(value: ast.AST) -> str | None:
    """元组字面量含冻结读 → 'frozen'; 含活读 → 'live'; 两者都有/都没有 → None。"""
    if not isinstance(value, (ast.Tuple, ast.List)):
        return None
    frozen = _contains(value, _is_frozen_read)
    live = _contains(value, _is_live_read)
    if frozen and not live:
        return "frozen"
    if live and not frozen:
        return "live"
    return None


def _sql_findings(path: str, node: ast.AST) -> list[Finding]:
    """S3: SQL 字符串里拿 ingest_batch 的冻结列与参数比相等。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                parts.append(str(piece.value))
            elif isinstance(piece, ast.FormattedValue) and isinstance(piece.value, ast.Name):
                parts.append("{" + piece.value.id + "}")
            else:
                parts.append("{...}")
        text = "".join(parts)
    else:
        return []
    if not _SQL_INGEST_ANY_RE.search(text) or _SQL_MUTATION_RE.match(text):
        return []
    aliases = {m.group(1) for m in _SQL_INGEST_RE.finditer(text)}
    aliases.discard("WHERE")
    aliases.discard("ON")
    found: list[Finding] = []
    for alias in aliases:
        if re.search(rf"\b{re.escape(alias)}\.(contract_hash|config_hash|source_name)\s*=\s*\?", text):
            found.append(
                Finding(
                    path, node.lineno, "sql_compare",
                    f"SQL 拿 ingest_batch 冻结列 ({alias}.contract_hash/config_hash/source_name) 与参数比相等",
                )
            )
    if not aliases:
        tables = {t.strip("{}") for t in _SQL_FROM_TABLES_RE.findall(text)}
        if tables <= {"ingest_batch", "INGEST_BATCH_TABLE"} and re.search(
            r"(?<![\w.])(contract_hash|config_hash|source_name)\s*=\s*\?", text
        ):
            found.append(
                Finding(
                    path, node.lineno, "sql_compare",
                    "SQL 拿 ingest_batch 冻结列 (contract_hash/config_hash/source_name) 与参数比相等",
                )
            )
    return found


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self._tuple_flags: dict[str, str] = {}

    # 每个函数一个名字作用域 (S2 的元组流只在函数内追踪)
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        saved = self._tuple_flags
        self._tuple_flags = {}
        self.generic_visit(node)
        self._tuple_flags = saved

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            flag = _tuple_flags(node.value)
            if flag is not None:
                self._tuple_flags[node.targets[0].id] = flag
            else:
                self._tuple_flags.pop(node.targets[0].id, None)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        operands = [node.left, *node.comparators]
        for op, left, right in zip(node.ops, operands[:-1], operands[1:], strict=True):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            if (_is_frozen_read(left) and _is_live_read(right)) or (
                _is_live_read(left) and _is_frozen_read(right)
            ):
                self.findings.append(
                    Finding(
                        self.path, node.lineno, "direct_compare",
                        f"冻结落地戳与活契约/指针比相等: {ast.unparse(node)[:120]}",
                    )
                )
                continue
            if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                flags = {self._tuple_flags.get(left.id), self._tuple_flags.get(right.id)}
                if flags == {"frozen", "live"}:
                    self.findings.append(
                        Finding(
                            self.path, node.lineno, "tuple_compare",
                            f"含冻结落地戳的元组与含活契约戳的元组比相等: {ast.unparse(node)[:120]}",
                        )
                    )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        self.findings.extend(_sql_findings(self.path, node))

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802
        self.findings.extend(_sql_findings(self.path, node))
        # 不再下钻: 内部的 Constant 片段单独看没有整句语义
        return


# ---------------------------------------------------------------------------
# 扫描入口
# ---------------------------------------------------------------------------


def scan_source(source: str, path: str) -> list[Finding]:
    tree = ast.parse(source, filename=path)
    scanner = _Scanner(path)
    scanner.visit(tree)
    return sorted(scanner.findings, key=lambda f: (f.path, f.line, f.kind))


def scan_file(path: Path, *, display_root: Path | None = None) -> list[Finding]:
    shown = str(path.relative_to(display_root)) if display_root else str(path)
    return scan_source(path.read_text(encoding="utf-8"), shown)


def iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        yield path


def scan_tree(root: Path, *, display_root: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_python_files(root):
        findings.extend(scan_file(path, display_root=display_root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="扫描根目录 (默认 backend/services)")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: root not a directory: {root}", file=sys.stderr)
        return 2
    findings = scan_tree(root, display_root=REPO if root.is_relative_to(REPO) else None)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(finding.render())
        print(
            f"check_frozen_stamp_compare: {len(findings)} finding(s) under {root}"
            + ("" if findings else " — PASS")
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
