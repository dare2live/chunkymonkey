"""沙盒边界守卫 — 防探索脚本意外写主库 (2026-06-17 用户: 边界要水密非约定)。

审计 wf_6185abb2 实测 BLOCKER: 沙盒脚本能裸 duckdb.connect(主库) 默认 read_write,
真在 856 万行 market 库建表删表, 零拦截。本模块把"读主库一律 read_only"从 README 约定
升级为 **运行时硬门** (defense in depth, 配 moth `sandbox-no-main-rw` grep 门)。

用法 (沙盒探索脚本首行):
    from services.sandbox_guard import enable_sandbox_guard, read_only_main, sandbox_scratch
    enable_sandbox_guard()                # 此后 read_write 打开主 6 库 = raise SandboxBoundaryError
    con = read_only_main("market")        # 读主库唯一正路 (强制 read_only=True)
    scr = sandbox_scratch("my_exp")       # 写探索数据 (sandbox/my_exp/scratch.duckdb, gitignored)

guard 是 opt-in (只在沙脚本显式调 enable 后生效), 主代码 (daily_update/services) 永不调 → 零影响。
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from services.database_manifest import get_database_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Current manifest databases that exploration must never open read-write.
_MAIN_ALIASES = ("smartmoney", "market", "tushare_raw", "feature_store", "experiment_store")

_ORIG_CONNECT = duckdb.connect
_GUARD_ON = False


class SandboxBoundaryError(RuntimeError):
    """沙盒脚本试图 read_write 打开主库 — 边界硬门拦截。"""


def _main_db_paths() -> dict[str, str]:
    mf = get_database_manifest()
    out: dict[str, str] = {}
    for alias in _MAIN_ALIASES:
        try:
            out[str(mf.path_for(alias).resolve())] = alias
        except Exception:
            continue
    return out


def enable_sandbox_guard() -> None:
    """monkeypatch duckdb.connect: 此后 read_write 打开主 6 库 raise; read_only + scratch 放行。"""
    global _GUARD_ON
    if _GUARD_ON:
        return
    mains = _main_db_paths()

    def _guarded(database=":memory:", read_only=False, **kwargs):
        try:
            resolved = str(Path(str(database)).resolve())
        except Exception:
            resolved = str(database)
        if resolved in mains and not read_only:
            raise SandboxBoundaryError(
                f"沙盒禁止 read_write 打开主库 {mains[resolved]} ({database})。"
                f" 读主库用 read_only_main('{mains[resolved]}'); 写探索数据用 sandbox_scratch(<exp>)。"
            )
        return _ORIG_CONNECT(database, read_only=read_only, **kwargs)

    duckdb.connect = _guarded  # type: ignore[assignment]
    _GUARD_ON = True


def disable_sandbox_guard() -> None:
    """还原 (主要供测试)。"""
    global _GUARD_ON
    duckdb.connect = _ORIG_CONNECT  # type: ignore[assignment]
    _GUARD_ON = False


def read_only_main(alias: str):
    """读主库唯一文档化正路 — 强制 read_only=True (绕过 guard 是安全的: 它本就只读)。"""
    mf = get_database_manifest()
    return _ORIG_CONNECT(str(mf.path_for(alias)), read_only=True)


def sandbox_scratch(exp_name: str):
    """写探索 scratch DB (per-exp: sandbox/<exp>/scratch.duckdb, gitignored, wipe 随探索一并清)。"""
    if not exp_name or "/" in exp_name or exp_name.startswith("."):
        raise ValueError(f"invalid exp_name: {exp_name!r}")
    p = _REPO_ROOT / "sandbox" / exp_name / "scratch.duckdb"  # rule-compliance: ok evidence=沙盒 per-exp scratch 隔离库路径, 非业务阈值/非主库 DB 边界 (sandbox gitignored 用完删)
    p.parent.mkdir(parents=True, exist_ok=True)
    return _ORIG_CONNECT(str(p), read_only=False)
