"""
Chunky Monkey v2 — 机构事件研究系统

FastAPI 入口
"""

import logging
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response

# 确保 backend 目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.db import init_db, get_conn
from services.dependency_guards import install_dependency_guards
from services.market_db import init_market_db

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cm-api")

install_dependency_guards()

# 初始化数据库（仅建表，不做迁移）
init_db()
init_market_db()

# 清理重启后遗留的“运行中”步骤状态，避免前端误以为仍在执行
try:
    _conn = get_conn()
    try:
        _conn.execute("""
            UPDATE step_status
            SET status = 'idle',
                started_at = NULL,
                finished_at = NULL,
                error = NULL,
                records = 0
            WHERE status = 'running'
        """)
        _conn.commit()
    finally:
        _conn.close()
except Exception:
    pass

# FastAPI app
app = FastAPI(title="ChunkyMonkey — 股票档案 / 主升浪猎手", version="2.0.0")


@app.on_event("startup")
async def _db_health_check():
    """Run the current DB index-consistency check during API startup.

    DuckDB ART secondary index 在 ON CONFLICT DO UPDATE / 异常中断写入时偶发
    跟 storage 不一致 (phantom rows in index). 启动检测可避免运行后 sync 路径
    因 phantom 触发 FATAL invalidate.
    """
    try:
        from services.db import get_conn
        from services.db_health import run_startup_checks
        _c = get_conn()
        try:
            summary = run_startup_checks(_c)
        finally:
            _c.close()
        import logging
        logging.getLogger("cm-startup").info("[db_health] startup checks: %s", summary)
    except Exception as exc:
        import logging
        # 索引在 REINDEX 后仍不一致才会抛 — 拒绝静默吞掉 (Rule 5: 不打补丁)
        logging.getLogger("cm-startup").error("[db_health] startup checks FAILED: %s", exc)
        raise

# CORS: 默认只允许本机 origin (本系统是单机本地工具, 写端点零鉴权,
# 不能用通配符 origin 让任意网站 CSRF 调用写接口). 需要跨机访问时
# 通过环境变量 CM_CORS_ORIGINS (逗号分隔) 显式放开.
import os as _os


def _resolve_cors_origins() -> list[str]:
    raw = _os.environ.get("CM_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    port = _os.environ.get("CM_PORT", "8000").strip() or "8000"
    # 默认本机 origin (loopback 的两种写法 + 默认端口)
    return [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# 当前路由是过渡期可执行面，不代表项目目的被缩成纯数据平台。Tier1-Tier4 只有在版本化
# contracts/release/decision evidence 闭合后才重新挂产品路由；Tier0 更新保持 manual-only。

# 手动任务触发 (数据采集/清洗链前端按钮手动跑)
from routers.ops_manual_run import router as ops_manual_run_router
app.include_router(ops_manual_run_router, prefix="/api/v3/ops", tags=["ops"])

# 模块化路由注册 (etf 模块 2026-06-29 批3d 整体退役物删; register_modules 保留供未来模块, 当前只读 enabled 状态)
def register_modules(app):
    try:
        conn = get_conn()
        from services.db import get_enabled_modules
        modules = get_enabled_modules(conn)
        conn.close()
    except Exception:
        modules = {"akquant": False}
    return modules

app_modules = register_modules(app)

# Legacy 手工观察账本：NONCONFORMING，不是 Tier4 paper execution/StrategyRelease。
from routers.paper_portfolio import router as paper_portfolio_router
app.include_router(paper_portfolio_router, prefix="/api/v3/paper", tags=["paper_portfolio"])

# Tier3 机构披露研究面：只展示 evidence，不发布候选或跟随信号。
from routers.institution_profile import router as inst_profile_router
app.include_router(inst_profile_router, prefix="/api/v3/inst", tags=["institution_profile"])

# Tier2 市场感知：资金热力/RS/广度/价格响应，只描述现状，零买卖暗示。
from routers.market_pulse import router as market_pulse_router
app.include_router(market_pulse_router, prefix="/api/v3/pulse", tags=["market_pulse"])

# 股票档案 MVP：分层读 form/holders；observation 为产品标签，不融 Tier0。
from routers.stock_dossier import router as stock_dossier_router
app.include_router(stock_dossier_router, prefix="/api/v3/stock", tags=["stock_dossier"])

# (退役 routers: market_perception/bestchoice/perception_legacy/signals_v2 等 2026-06-14~28 删, 详 ledger + git史)

# 设置选项相关的API (比如开启/关闭功能模块)
@app.post("/api/settings/modules")
async def toggle_modules(settings: dict):
    try:
        conn = get_conn()
        rows = [
            (f"module_{k}_enabled", "1" if v else "0")
            for k, v in settings.items()
            if k in {"akquant"}
        ]
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                rows,
            )
        conn.commit()
        conn.close()
        return {"status": "ok", "message": "配置已保存，请重启后端服务生效"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def build_health_payload() -> dict:
    try:
        conn = get_conn()
        from services.db import get_enabled_modules
        current_modules = get_enabled_modules(conn)
        conn.close()
    except Exception:
        current_modules = app_modules

    enabled = [k for k, v in current_modules.items() if v]
    return {
        "status": "ok",
        "enabled_modules": enabled,
        "available_modules": ["akquant"],
        "module_deps": {"akquant": "远期规划"}
    }


# 健康检查
@app.get("/health")
async def health():
    return build_health_payload()


@app.get("/api/inst/health/summary")
async def inst_health_summary():
    return build_health_payload()


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# 静态文件
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 前端沿革: 旧 vanilla JS 前端(index.html+assets/js/*, 挂/assets)2026-07-07 全库死代码普查
# 簇1+2 整体退役物删(render_index_html/build_index_asset_version 无任何路由调用, 确认孤儿);
# 旧 v3 React 设计稿已归档 .archive/; dossier 视图 2026-06-28 退役 (根路由曾指它 → 307→404
# = 用户"双击 start.command 无法启动"的真相, 2026-07-03 修)。
# 现行唯一前端 = edge React (frontend/, 2026-07-02): 生产 build 产物挂 /app (vite base=/app/,
# 避开旧 dossier /assets 挂载); 改前端后 cd frontend && npm run build 刷新产物。
_EDGE_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _EDGE_DIST.exists():
    app.mount("/app", StaticFiles(directory=str(_EDGE_DIST), html=True), name="edge_app")


@app.get("/")
async def index():
    """根路径进现有观察前端；research/legacy 页面不等于 Tier4 决策产品。"""
    from fastapi.responses import RedirectResponse
    if _EDGE_DIST.exists():
        return RedirectResponse(url="/app/")
    return {"error": "edge 前端产物缺失", "fix": "cd frontend && npm install && npm run build"}


@app.get("/v3", status_code=410)
@app.get("/v3/", status_code=410)
@app.get("/legacy", status_code=410)
@app.get("/legacy/", status_code=410)
async def retired_frontends():
    """旧前端退役收口；历史设计稿只保留在 git 历史中。
    当前唯一前端 = /app/ (edge React)。2026-07-07 修: 此前误指向已随2026-06-28重建物删的
    /api/dossier/view (307→404 断链), 改 410 Gone + redirect 字段指现行前端。"""
    return {"error": "legacy_retired", "redirect": "/app/"}
