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
app = FastAPI(title="Chunky Monkey v2", version="2.0.0")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from routers.market import router as market_router
from routers.institution import router as institution_router
from routers.updater import router as updater_router

app.include_router(market_router, prefix="/api/inst", tags=["market"])
app.include_router(institution_router, prefix="/api/inst", tags=["institution"])
app.include_router(updater_router, prefix="/api/inst", tags=["updater"])

from routers.etf import router as etf_router

# 模块化路由注册
def register_modules(app):
    try:
        conn = get_conn()
        from services.db import get_enabled_modules
        modules = get_enabled_modules(conn)
        conn.close()
    except Exception:
        modules = {"etf": True, "akquant": False}

    if modules.get("etf"):
        app.include_router(etf_router, prefix="/api/etf", tags=["etf"])

    return modules

app_modules = register_modules(app)

from routers.screening import router as screening_router
app.include_router(screening_router, prefix="/api/screening", tags=["screening"])

from routers.signals import router as signals_router
app.include_router(signals_router, prefix="/api/signals", tags=["signals"])

from routers.recommendation import router as recommendation_router
app.include_router(recommendation_router, prefix="/api/rec", tags=["recommendation"])

from routers.workbench import router as workbench_router
app.include_router(workbench_router, prefix="/api/workbench", tags=["workbench"])

# 数据源 registry (P1)
from routers.data_sources import router as data_sources_router
app.include_router(data_sources_router, prefix="/api/data_sources", tags=["data_sources"])

# 策略预设 (P4)
from routers.strategy_preset import router as strategy_preset_router
app.include_router(strategy_preset_router, prefix="/api/inst/strategy", tags=["strategy_preset"])

# v3 设计稿专用聚合 API
from routers.v3_meta import router as v3_meta_router
app.include_router(v3_meta_router, prefix="/api/v3", tags=["v3_meta"])

# Phase γ D4: 股票画像 + trade plan (mart_stock_picture_daily / mart_stock_trade_plan)
from routers.v3_picture import router as v3_picture_router
app.include_router(v3_picture_router, prefix="/api/v3", tags=["v3_picture"])

# Phase δ D4: paper engine (mart_paper_nav / fact_paper_position / mart_signal_ic / mart_decision_outcome)
from routers.v3_paper import router as v3_paper_router
app.include_router(v3_paper_router, prefix="/api/v3/paper", tags=["v3_paper"])

# Phase ε D3: 优选追踪 + 反馈闭环
from routers.v3_selection import router as v3_selection_router
app.include_router(v3_selection_router, prefix="/api/v3/selection", tags=["v3_selection"])

# Phase η: 3 视图 API (股票/公式/机构)
from routers.v3_views import router as v3_views_router
app.include_router(v3_views_router, prefix="/api/v3/view", tags=["v3_view"])

# Phase η++ : 组合构建器 (3 risk profile, Kelly + Wilson 仓位)
from routers.v3_portfolio_builder import router as v3_portfolio_builder_router
app.include_router(v3_portfolio_builder_router, prefix="/api/v3/portfolio", tags=["v3_portfolio"])

# 市场感知 (Market Perception): standalone project at /stock/perception.
PERCEPTION_SRC = Path(__file__).resolve().parents[2] / "perception" / "src"
if PERCEPTION_SRC.exists() and str(PERCEPTION_SRC) not in sys.path:
    sys.path.insert(0, str(PERCEPTION_SRC))
try:
    from perception.router import router as v3_market_perception_router
except Exception as exc:
    logger.warning("standalone perception router unavailable, falling back to bundled router: %s", exc)
    from routers.v3_market_perception import router as v3_market_perception_router
app.include_router(v3_market_perception_router, prefix="/api/v3/market_perception", tags=["v3_market_perception"])

# Project D 股票图谱 (2026-05-22 用户新加): UI 查询层, 基于 Perception 7 mart, 不接 ranker
from routers.stock_graph import router as stock_graph_router
app.include_router(stock_graph_router, prefix="/api/v3", tags=["v3_stock_graph"])

# 初始化 signals_v2 默认配置（幂等）
try:
    _conn = get_conn()
    try:
        from services.signals_v2 import ensure_defaults as _signals_v2_defaults
        _signals_v2_defaults(_conn)
    finally:
        _conn.close()
except Exception as _e:
    logger.warning(f"signals_v2 ensure_defaults failed: {_e}")

# 设置选项相关的API (比如开启/关闭功能模块)
@app.post("/api/settings/modules")
async def toggle_modules(settings: dict):
    try:
        conn = get_conn()
        rows = [
            (f"module_{k}_enabled", "1" if v else "0")
            for k, v in settings.items()
            if k in {"etf", "akquant"}
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
        "available_modules": ["etf", "akquant"],
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

# v3 设计稿（React CDN，零工具链，详见 开发手册.md §7）
DESIGN_DIR = PROJECT_ROOT / "design"
if DESIGN_DIR.exists():
    app.mount("/v3", StaticFiles(directory=str(DESIGN_DIR), html=False), name="v3-design")


@app.get("/")
async def index():
    """根路径直接进 v3 设计稿 (旧 vanilla 前端已 Phase ζ 退役)。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/v3/Chunky%20Monkey%20v3.html")


@app.get("/legacy")
@app.get("/legacy/")
async def legacy_retired():
    """Phase ζ 收尾: 旧 vanilla 前端正式退役。文件保留在 repo 历史, 但不再对外提供。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=410,
        content={
            "ok": False,
            "error": "legacy_retired",
            "message": "旧 vanilla 前端已 Phase ζ 退役, 请使用 /v3",
            "redirect": "/v3/Chunky%20Monkey%20v3.html",
        },
    )


@app.get("/v3")
@app.get("/v3/")
async def v3_index():
    """v3 设计稿入口，重定向到主 HTML。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/v3/Chunky%20Monkey%20v3.html")
