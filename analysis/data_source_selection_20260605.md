# Data Source Selection Note - 2026-06-05

Scope: compare Tonghuashun-oriented MCP/iFinD data, Tongdaxin MCP/TDX protocol data, and TuShare for ChunkyMonkey's current data-source gaps.

## Controller Verdict

Use a split decision, not one universal vendor:

| Use case | Preferred path | Reason | Current production status |
|---|---|---|---|
| `need_027` exact stock-level main/super-large/large/medium/small order flow | TuShare read-only probe first | TuShare documents daily stock money-flow interfaces with historical rows and explicit large-order buckets. `moneyflow` has buy/sell small, medium, large, extra-large fields since 2010; `moneyflow_dc` has main, extra-large, large, medium, small net fields from 2023-09-11. | Blocked until a local token-backed probe passes field/date/row stability plus PIT/freshness/writer/watermark gates. |
| Industry/theme rotation, leader diffusion, hot concept tracking | Tonghuashun semantic data first, with TuShare THS/DC interfaces as the lowest-friction structured route | TuShare exposes THS index/member/daily, THS industry/concept money flow, THS hot list, THS limit-up list, and strongest concept statistics. Direct iFinD/Tonghuashun can be richer but has heavier account/licensing integration. | Research/snapshot-only until daily PIT snapshot contracts exist. |
| Low-cost market/TDX backbone | Existing `tdxhub` plus experimental Tongdaxin MCP/xmtdx only behind probes | Project already uses TDX for K-line, F10, gpcw, xdxr, and block/industry truth. `xmtdx` advertises current and historical fund-flow commands, but this is a protocol-level community package and must be validated against field semantics, coverage, stability, and license constraints before it can affect `need_027`. | Keep as backbone/experimental candidate, not a `need_027` production replacement yet. |

## Project Facts

- `backend/config/tdx_data_need_coverage.yaml` keeps `need_027` blocked: current source is stale `raw_fund_flow_daily`, current TDX path does not provide the required capability, PIT is unknown, and production eligibility is blocked.
- `docs/chunkyctl_session_quickstart.md` requires `probe_source_capability.py --need027-exact-flow-gate` before treating `need_027` as production evidence.
- `backend/services/data_sources/clients_registry.py` currently registers `tdxhub`, `aif10`, `akshare`, and derived writers; TuShare and direct Tonghuashun/iFinD are not yet local source clients.
- `aif10` is not a valid exact-flow fallback today because it lacks `individual_fund_flow`; AkShare exact flow remains unstable in live probes.

## External Evidence Checked

- TuShare `moneyflow`: stock-level money flow starts from 2010 and includes small, medium, large, and extra-large buy/sell amounts; threshold rules are documented as small < 50k, medium 50k-200k, large 200k-1m, extra-large >= 1m.
- TuShare `moneyflow_dc`: daily Eastmoney stock money flow starts from 2023-09-11 and includes main, extra-large, large, medium, and small net fields.
- TuShare THS/DC rotation endpoints include `ths_index`, `ths_member`, `ths_daily`, `moneyflow_ind_ths`, `moneyflow_cnt_ths`, `ths_hot`, `limit_list_ths`, `limit_cpt_list`, `tdx_index`, and `dc_member`.
- Tonghuashun iFinD exposes formal HTTP/Python endpoints for real-time quotes, historical quotes, basic data, data pools, snapshot/tick data, reports, smart stock picking, and trade-date queries; it is likely richer but heavier to authorize and operationalize.
- `tdx-mcp` is an MCP wrapper for public Tongdaxin quote servers and declares non-commercial learning-use constraints.
- `xmtdx` advertises `get_fund_flow()` and `get_history_fund_flow()` with extra-large/large/medium/small order-flow support, but it is not yet project-validated and should be treated as a probe candidate.

## Minimal Reversible Next Step

Do not add a writer yet.

1. Add a read-only candidate-source probe adapter shape that maps candidate provider rows into the existing `need_027` exact-flow contract:
   - `stock_code`
   - `trade_date`
   - `main_net_amount`
   - `super_large_net_amount`
   - `large_net_amount`
   - `medium_net_amount`
   - `small_net_amount`
   - source watermark and raw field lineage
2. First probe TuShare using the existing three sample stocks (`600519`, `000001`, `300750`) if a token is available.
3. Only after the probe passes should the controller design a persistent writer, watermark table, PIT/freshness evidence, and failure-queue resolution path.
4. For industry/theme/leader diffusion, define a daily snapshot contract first; do not backtest against current-only concept membership.

## Recommendation

| Rank | Source | Recommendation |
|---|---|---|
| 1 | TuShare | Best first probe for `need_027` and a practical structured route for THS/DC rotation data, assuming token/points are available. |
| 2 | Tonghuashun direct iFinD/MCP | Best semantic source for industry chain, themes, hotness, and leader diffusion if licensing/account access is acceptable; do not depend on it before contract and cost are known. |
| 3 | Tongdaxin MCP/xmtdx | Keep as low-cost experimental probe and TDX backbone extension; not enough local evidence to promote it for `need_027` yet. |

Durable owner: source priority and production eligibility belong in `backend/config/tdx_data_need_coverage.yaml`; local client metadata belongs in `backend/services/data_sources/clients_registry.py`; proof belongs in `probe_source_capability.py`, `mart_data_source_failure_queue`, and future PIT/freshness audit outputs.
