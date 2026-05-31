# Perception External Module

Market perception is maintained as a standalone project:

`/Users/dp/Documents/M/stock/perception`

## Runtime Contract
- ChunkyMonkey keeps the existing UI/API entry unchanged.
- API prefix remains `/api/v3/market_perception`.
- The `市场感知` tab in `design/Chunky Monkey v3.html` remains unchanged.
- `backend/main.py` prefers the standalone router from `/stock/perception/src/perception/router.py`.
- If the standalone router cannot be loaded, ChunkyMonkey falls back to the bundled legacy router.

## Standalone Project Layout
- `src/perception/market_perception`: engines.
- `src/perception/router.py`: FastAPI router used by ChunkyMonkey.
- `config/market_perception.yaml`: market perception config.
- `scripts/`: standalone builders and audits.
- `tests/market_perception`: standalone tests.
- `design/v3-page-market-perception.jsx`: UI source mirror for the existing ChunkyMonkey tab.

## Validation
```bash
cd /Users/dp/Documents/M/stock/perception
PYTHONPATH=src:/Users/dp/Documents/M/stock/chunkymonkey/backend python -m pytest -q tests/market_perception
```

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey
PYTHONPATH=/Users/dp/Documents/M/stock/perception/src:backend python -m pytest -q backend/tests/services/market_perception backend/tests/contract/test_workbench_frontend_contract.py
```

## Boundary
Perception only writes/reads `mart_market_perception_*` context outputs. It does not own ranker, panel, paper_sim, promotion, GCP, or trading decisions.
