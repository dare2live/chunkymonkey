"""Legacy lifeboat endpoints mounted by the updater router."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

logger = logging.getLogger("cm-api")
router = APIRouter()

_lifeboat_running = False
_lifeboat_result = None


def _lifeboat_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "lifeboat"


@router.post("/lifeboat/run")
async def run_lifeboat():
    """运行救生艇脚本（异步），返回运行状态"""
    global _lifeboat_running, _lifeboat_result
    if _lifeboat_running:
        return {"ok": False, "message": "救生艇正在运行中，请稍候"}

    script_path = _lifeboat_dir() / "fetch_and_report.py"
    if not script_path.exists():
        return {"ok": False, "message": f"救生艇脚本不存在: {script_path}"}

    _lifeboat_running = True
    _lifeboat_result = None

    async def _run():
        global _lifeboat_running, _lifeboat_result
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(script_path.parent),
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if proc.returncode == 0:
                _lifeboat_result = {
                    "ok": True,
                    "message": "救生艇报告已生成",
                    "output": output[-500:],
                }
                logger.info("[救生艇] 运行完成")
            else:
                _lifeboat_result = {
                    "ok": False,
                    "message": f"运行失败 (exit {proc.returncode})",
                    "output": output[-500:],
                }
                logger.error(f"[救生艇] 失败: {output[-200:]}")
        except Exception as exc:
            _lifeboat_result = {"ok": False, "message": str(exc)}
            logger.error(f"[救生艇] 异常: {exc}")
        finally:
            _lifeboat_running = False

    asyncio.create_task(_run())
    return {"ok": True, "message": "救生艇已启动，请稍候约2分钟"}


@router.get("/lifeboat/status")
async def lifeboat_status():
    """查询救生艇运行状态"""
    if _lifeboat_running:
        return {"running": True, "result": None}
    return {"running": False, "result": _lifeboat_result}


@router.get("/lifeboat/report")
async def lifeboat_report():
    """返回救生艇 HTML 报告内容"""
    report_path = _lifeboat_dir() / "report.html"
    if not report_path.exists():
        return Response(content="<h3>尚未生成救生艇报告。请先运行。</h3>", media_type="text/html")
    return FileResponse(str(report_path), media_type="text/html")
