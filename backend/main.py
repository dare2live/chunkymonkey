"""
Chunky Monkey v2 — 机构事件研究系统

FastAPI 入口
"""

import logging
import sys
import hashlib
import re
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response

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
    """Phase ψ.5 根因 2: 启动时跑 DB 索引一致性检查 + 清冗余索引.

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

# 注册路由 — 2026-06-28 重建(白名单裁剪): 策略/serving routers 全退役 (signals/dossier/v3_picture/
#   v3_paper/v3_selection/v3_portfolio_builder/stock_graph/workbench/market), 项目降为纯数据平台。
#   只留 ops(手动跑数据链) + v3_config(前端参数下发) 两个 routers; 数据走 pipeline + SERVE(data_access)。

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

# 配置 routers (v3 配置)
# (strategy_preset router 批7 物删, 详 ledger)
from routers.v3_config import router as v3_config_router
app.include_router(v3_config_router, prefix="/api/v3", tags=["v3_config"])

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
ASSETS_DIR = PROJECT_ROOT / "assets"
INDEX_HTML = PROJECT_ROOT / "index.html"
INDEX_ASSET_FILES = [
    INDEX_HTML,
    ASSETS_DIR / "css" / "main.css",
    ASSETS_DIR / "js" / "style-tokens.js",
    ASSETS_DIR / "js" / "app-cache.js",
    ASSETS_DIR / "js" / "app-nav.js",
    ASSETS_DIR / "js" / "app-list-state.js",
    ASSETS_DIR / "js" / "signal-adapter.js",
    ASSETS_DIR / "js" / "stock-view.js",
    ASSETS_DIR / "js" / "data-view.js",
    ASSETS_DIR / "js" / "strategy-view.js",
    ASSETS_DIR / "js" / "settings-view.js",
    ASSETS_DIR / "js" / "app.js",
]
INDEX_ASSET_VERSION_PATTERN = re.compile(
    r"(window\.CM_ASSET_VERSION\s*=\s*')[^']+('\s*;)"
)


def build_index_asset_version() -> str:
    hasher = hashlib.sha1()
    for path in INDEX_ASSET_FILES:
        if not path.exists():
            continue
        stat = path.stat()
        hasher.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(stat.st_mtime_ns).encode("utf-8"))
    return hasher.hexdigest()[:12]


def render_index_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    asset_version = build_index_asset_version()
    rendered_html, replacements = INDEX_ASSET_VERSION_PATTERN.subn(
        lambda match: f"{match.group(1)}{asset_version}{match.group(2)}",
        html,
        count=1,
    )
    if replacements != 1:
        logger.warning("index.html 未找到唯一的 CM_ASSET_VERSION 注入点，返回原始内容")
        return html
    return rendered_html

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# 旧 v3 React 设计稿 (design/) 已退役归档 .archive/design_pre_reset_v3/ — 残留审计 wf_9e0eebb2 确证:
# main.py 根路由曾重定向到 design/ 旧 v3 React 界面 = 用户痛点"打开看到旧前端误导"。
# 当前唯一 live 前端 = 股票档案 (Stock Dossier) /api/dossier/view。/v3 StaticFiles 挂载已删。


@app.get("/")
async def index():
    """根路径进股票档案 (Stock Dossier) — 当前唯一 live 前端。旧 v3 React 设计稿/vanilla 前端均已退役 (design/ 归档 .archive/)。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/dossier/view")


@app.get("/v3")
@app.get("/v3/")
@app.get("/legacy")
@app.get("/legacy/")
async def retired_frontends():
    """旧前端退役收口: v3 React 设计稿 → .archive/design_pre_reset_v3/; 旧 vanilla → repo 历史。当前前端 = /api/dossier/view。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/dossier/view")
