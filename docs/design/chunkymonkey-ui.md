# ChunkyMonkey UI Design System

## Product Shape

ChunkyMonkey is an institutional event research cockpit. The interface should make the current research state scannable before asking the user to read long explanations.

Visible primary navigation is limited to:

- 今日研究
- 数据链路
- 模型实验室
- 系统运维

Advanced data health, strategy, and settings tools remain reachable from 系统运维, not from the top-level navigation.

## Visual Rules

- No visible emoji in application UI. Use CSS dots, pills, bars, heatmaps, and timelines.
- Use compact tables and status bands before prose. First viewport should answer what changed, what is healthy, and what needs action.
- Cards are reserved for repeated entities and compact operational panels. Large page regions stay unframed.
- Tables must sit in horizontal scroll containers on narrow screens.
- Status colors:
  - OK: green dot or pill.
  - WARN: amber dot or pill.
  - FAIL: red dot or pill.
  - INFO/UNKNOWN: neutral or brand-tinted pill.

## Shared JavaScript Primitives

`assets/js/security-identity.js` owns stock identity display:

- `formatMarketCode(security)` returns `SZ: 002309`, `SH: 600000`, or `BJ: 8xxxxx`.
- `xueqiuUrl(security)` returns `https://xueqiu.com/S/SZ002309` for known A-share markets.
- `renderSecurityIdentity(security, opts)` renders stock name plus market-code link.
- Missing stock names render as `名称待补`.

Old helpers in `assets/js/app.js` delegate to `SecurityIdentity` so legacy and new views use the same format.

`assets/js/viz-primitives.js` owns compact chart markup:

- `miniBar`
- `stackedBar`
- `sparkline`
- `heatmap`
- `timeline`
- `gateRail`
- `waterfall`

These helpers are intentionally small HTML/SVG string builders so existing plain-JS views can adopt them without a framework migration.

## Research Page

今日研究 defaults to champion-only recommendations. Challenger output is described as shadow or experiment-only and must not be mixed into production TopK rows.

Stock rows, drawer titles, TopK chips, and TopK summaries must show:

```text
股票名称
SZ: 002309
```

The market code links to Xueqiu and opens in a new tab with `rel="noopener"`.

## Data Lineage Page

数据链路 is the primary operational cockpit. It must show:

- The business data item.
- The landing table.
- Current source and protocol.
- Connectivity state.
- Fallback source and fallback state.
- Asset health and freshness.
- Update step.
- Repair entry.

The page also includes:

- Asset health heatmap by layer.
- Source priority and connectivity table.
- Fallback transition panel.
- Schema drift queue for derived and experiment tables.

## Verification

Before shipping UI changes, run:

```bash
git diff --check
node --check assets/js/security-identity.js
node --check assets/js/viz-primitives.js
node --check assets/js/stock-view.js
node --check assets/js/widgets/topk-strip.js
node --check assets/js/data-view.js
node --check assets/js/data-health-view.js
python3 -m py_compile backend/main.py backend/routers/recommendation.py backend/routers/data_sources.py backend/routers/data_health.py
```

Also scan visible UI assets for emoji and verify desktop 1280px plus mobile 390px layouts in a browser.
