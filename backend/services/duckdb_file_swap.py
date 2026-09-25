"""共用的"建到新文件后原子换名"机制 (cut_qfq_fresh_file_swap)。

背景 (spec `sandbox/churn_fix_20260919/spec_derived_rebuild_churn.md` §2/§4):
同文件 DROP+CTAS(+索引) 的写者, checkpoint 时旧块整批入 free list, 隔日 50% 空洞;
带 ART 索引时索引块与表块交错落位, 文件永久钉在 2×。根治不是"事后压缩", 是
"往一个全新文件里建, 校验过再原子换名" —— 换名本身 (`os.replace`) 零拷贝、
建库期间生产文件只被只读 ATTACH, 从不被写锁挡。

两个生产写者共用这一份换名机制 (不各写一遍): `build_price_kline_qfq_tushare.py`
与 `db_compact.py`。不放进 `services/duckdb_compact.py`——那是压缩刀 (measure +
一次性压缩尝试), qfq 建库刀不 import 它, 两者只共享这个模块。

`swap_in_fresh_file` 六项检查按顺序执行, **任一不过就抛 `SwapRefused`, 且在抛出
之前不做任何文件系统写操作** (检查阶段全部是只读探测); 全部检查通过后才依次做
硬链接 (可选) 与最终的 `os.replace`。本模块除最后 `os.replace` 一步外, 不允许出现
任何 rename/replace/unlink 生产文件的写法——换名只有这一条路。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import duckdb

# ── SwapRefused.reason 固定枚举 (测试逐个断言, 不是自由文本) ────────────────────
# 六个值一一对应 W1 的六个隔离用例 (build.wal 残留 / live.wal 残留 / live 指纹变 /
# 首次建库 expected 非 None / live 有活写者 / keep_bak 已存在)。
REASON_BUILD_NOT_CLOSED = "build_not_closed"
REASON_STALE_LIVE_WAL = "stale_live_wal"
REASON_LIVE_CHANGED_DURING_BUILD = "live_changed_during_build"
REASON_UNEXPECTED_FIRST_BUILD = "unexpected_first_build"
REASON_LIVE_HAS_ACTIVE_WRITER = "live_has_active_writer"
REASON_BAK_ALREADY_EXISTS = "bak_already_exists"

SWAP_REFUSAL_REASONS = (
    REASON_BUILD_NOT_CLOSED,
    REASON_STALE_LIVE_WAL,
    REASON_LIVE_CHANGED_DURING_BUILD,
    REASON_UNEXPECTED_FIRST_BUILD,
    REASON_LIVE_HAS_ACTIVE_WRITER,
    REASON_BAK_ALREADY_EXISTS,
)


class SwapRefused(Exception):
    """六项围栏之一未通过——生产文件与 build 文件均未被触碰。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def file_fingerprint(path: Path) -> tuple[int, int, int]:
    """(st_ino, st_size, st_mtime_ns) —— 只读打开/只读 ATTACH 不改变它;
    另一写者提交并 checkpoint 后它必变 (见 spec §0 主循环实测前提)。"""
    st = path.stat()
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _wal_path(path: Path) -> Path:
    return path.with_name(path.name + ".wal")


def swap_in_fresh_file(
    build: Path,
    live: Path,
    *,
    expected: Optional[tuple[int, int, int]],
    keep_bak: Optional[Path] = None,
) -> None:
    """把已经建好、校验过的 `build` 原子换名成 `live`。

    `expected` 是调用方在**开始建 build 之前**记的 `live` 指纹 (`live` 当时不存在则
    传 `None`); 用来抓"建库期间 live 被另一个写者动过"。`keep_bak` 给出时, 在换名前
    对旧 `live` 打一个硬链接保留 (不复制, 保住旧 inode), 调用方负责后续按需删除。

    六项检查全部只读, 顺序固定, 任一不过立即抛 `SwapRefused` 且不写任何文件:
      1. build 存在且 build.wal 不存在 (build 连接没干净关闭 → 拒绝)。
      2. live.wal 不存在 (残留 WAL 会在换名后被回放进新文件, 见 spec E5b 实测)。
      3. live 存在时指纹必须等于 expected; live 不存在时 expected 必须是 None
         (首次建库场景)。
      4. live 存在时, 以 read_only=True 新开一次连接立即关闭, 探测是否有活跃写者
         (捕获锁错误 duckdb.IOException; 同进程持未关连接会是 duckdb.ConnectionException
         的"different configuration"冲突, 同样按拒绝处理)。
      5. `keep_bak` 非 None 且 live 存在时, `keep_bak` 目标路径不得已存在。
    通过后: 5b) 如需要, `os.link(live, keep_bak)`；6) `os.replace(build, live)`。
    """
    build_wal = _wal_path(build)
    live_wal = _wal_path(live)

    if not build.exists() or build_wal.exists():
        raise SwapRefused(
            REASON_BUILD_NOT_CLOSED,
            f"build not ready to swap: build={build} exists={build.exists()} "
            f"wal={build_wal} exists={build_wal.exists()}",
        )

    if live_wal.exists():
        raise SwapRefused(REASON_STALE_LIVE_WAL, f"live wal present: {live_wal}")

    live_exists = live.exists()
    if live_exists:
        actual = file_fingerprint(live)
        if actual != expected:
            raise SwapRefused(
                REASON_LIVE_CHANGED_DURING_BUILD,
                f"live fingerprint changed: expected={expected} actual={actual}",
            )
    elif expected is not None:
        raise SwapRefused(
            REASON_UNEXPECTED_FIRST_BUILD,
            f"live does not exist but expected={expected} was given",
        )

    if live_exists:
        try:
            # 用裸 duckdb.connect 而非 services.duck_adapter.connect: 后者对锁冲突默认
            # 重试最多 30s, 这里要的是立即探测 (有活写者就该马上拒绝, 不是等它让开)。
            probe = duckdb.connect(str(live), read_only=True)  # rule-compliance: ok evidence=只读探测活跃写者, 立即失败不重试, 非业务阈值/非主库写
            probe.close()
        except duckdb.Error as exc:
            raise SwapRefused(REASON_LIVE_HAS_ACTIVE_WRITER, str(exc)) from exc

    if keep_bak is not None and live_exists and keep_bak.exists():
        raise SwapRefused(REASON_BAK_ALREADY_EXISTS, f"bak already exists: {keep_bak}")

    # ── 全部检查通过, 以下才是真正的写操作 ──────────────────────────────────
    if keep_bak is not None and live_exists:
        os.link(live, keep_bak)  # 硬链接保住旧 inode, 不复制内容
    os.replace(build, live)
