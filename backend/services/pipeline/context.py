"""管线共享上下文 + 阶段执行助手 (2026-06-23 daily_update 重设计)。

替代旧 bash 的 log() / step_degraded() / DEGRADED_FLAG / run_script 机制 — 语义保持一致:
- 所有步骤失败 = degraded (记录 + 续跑, 不中断链), 链尾汇总 + 告警送达 (宪法第5条; 防静默断流)。
- log 同时写 /tmp 日志文件 + stdout。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# degraded 告警 flag (session 启动检查 /tmp/chunkymonkey_ALERT_*.flag); 每次链起跑清, 跑完仍存=本次真实降级
DEGRADED_FLAG = Path("/tmp/chunkymonkey_ALERT_daily_update_degraded.flag")


def db_path(alias: str) -> str:
    """库别名 → 路径 (database_manifest 单一真相源, 不 hardcode .duckdb 字面量)。"""
    from services.database_manifest import get_database_manifest
    return str(get_database_manifest().path_for(alias))


@dataclass
class PipelineContext:
    """一次 daily_update 运行的共享状态。"""

    dry: bool = False
    skip_sync: bool = False
    date: str = ""  # YYYYMMDD
    log_path: Path | None = None
    degraded_msgs: list[str] = field(default_factory=list)
    _log_fh: object = None

    def __post_init__(self):
        if not self.date:
            raise ValueError("PipelineContext.date 必填 (YYYYMMDD; 由 run.py 注入, 不取 wall-clock 防跨午夜)")
        if self.log_path is None:
            self.log_path = Path(f"/tmp/chunkymonkey_daily_update_{self.date}.log")
        # 追加模式打开日志 (与旧 bash tee -a 一致)
        self._log_fh = open(self.log_path, "a", encoding="utf-8")

    # ── 日志 / 降级 ──────────────────────────────────────────────
    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self._log_fh:
            self._log_fh.write(line + "\n")
            self._log_fh.flush()

    def degraded(self, msg: str) -> None:
        """degraded 级失败: 记录 + 续跑 + 写 flag (链尾汇总送达)。旧 || log WARN 吞错=断流根因。"""
        self.log(f"DEGRADED: {msg}")
        self.degraded_msgs.append(msg)
        try:
            with open(DEGRADED_FLAG, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%F %T')}] {msg}\n")
        except Exception:  # noqa: BLE001 — flag 写失败不能把 degraded 升级成链中断
            pass

    def reset_degraded_flag(self) -> None:
        """链起跑清前次 flag (本次跑完仍存在 = 本次产生的真实降级)。"""
        DEGRADED_FLAG.unlink(missing_ok=True)

    # ── 执行助手 ──────────────────────────────────────────────
    def db(self, alias: str) -> str:
        return db_path(alias)

    def run_script(self, rel_path: str, args: list[str] | None = None, *, degraded_msg: str) -> bool:
        """subprocess 跑 backend/scripts 独立脚本 (隔离 DuckDB 写连接; 输出进日志)。
        返回 True=成功; False=失败已记 degraded。skip_sync/dry 由调用方在 stage 内判。
        """
        cmd = [sys.executable, rel_path] + (args or [])
        self.log(f"  $ {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd, cwd=str(REPO), capture_output=True, text=True,
                env=self._subprocess_env(),
            )
        except Exception as e:  # noqa: BLE001
            self.degraded(f"{degraded_msg} (subprocess 异常 {e})")
            return False
        if self._log_fh:
            self._log_fh.write(proc.stdout or "")
            self._log_fh.write(proc.stderr or "")
            self._log_fh.flush()
        if proc.returncode != 0:
            self.degraded(f"{degraded_msg} (exit {proc.returncode})")
            return False
        return True

    def _subprocess_env(self) -> dict:
        import os
        env = dict(os.environ)
        # PYTHONPATH=backend (子脚本 import services.*); 继承 .env (bash 已 source)
        env["PYTHONPATH"] = "backend" + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return env

    def step(self, fn, *, degraded_msg: str) -> bool:
        """跑一个 in-process 步骤函数 (直调 service); 异常→degraded 续跑。"""
        try:
            fn()
            return True
        except Exception as e:  # noqa: BLE001
            import traceback
            self.log(traceback.format_exc())
            self.degraded(f"{degraded_msg} ({e})")
            return False

    def close(self) -> None:
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
