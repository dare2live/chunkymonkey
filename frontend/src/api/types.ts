/** TS interfaces — 真相源 = 后端真实返回 (2026-07-02 TestClient 实测 GET 端点 + 读代码核 POST/DELETE)。
 *
 * 实测来源:
 *   GET /api/v3/inst/profiles, /profiles/{holder}, /signals — TestClient 200 真实 JSON
 *   GET /api/v3/paper/portfolio, /nav — TestClient 200 (空池, 结构自 routers/paper_portfolio.py 列名 zip)
 * 读代码来源 (POST/DELETE 不实弹):
 *   POST /paper/positions → services/paper_portfolio.add_position 返回 dict
 *   DELETE /paper/positions/{id} → close_position 返回 dict
 *   POST /paper/mark → mark_to_market 返回 dict
 */

// ── C1 机构档案 ────────────────────────────────────────────────────────────

/** GET /api/v3/inst/profiles → {status, profiles: InstProfileRow[]} */
export interface InstProfileRow {
  holder: string;
  holder_type: string | null;
  n_closed: number;
  median_alpha: number | null;
  avg_alpha: number | null;
  win_rate_alpha: number | null;
  median_ret: number | null;
  avg_hold_days: number | null;
  low_sample: boolean;
}

/** profile.dims[] — dim_type 实测三值: industry_pit / year / holder_type */
export interface InstProfileDim {
  dim_type: "industry_pit" | "year" | "holder_type";
  dim_value: string;
  n_closed: number;
  median_alpha: number | null;
  win_rate_alpha: number | null;
  low_sample: boolean;
}

/** profile.episodes[] — 日期为 YYYYMMDD 字符串 (实测 "20240331"); holding 态 close_date=null */
export interface InstEpisode {
  stock: string;
  open_date: string;
  close_date: string | null;
  status: "closed" | "holding";
  ret_c1: number | null;
  alpha_c1: number | null;
  n_adds: number;
  n_trims: number;
  sw_l1_at_open: string | null;
  seeded: boolean;
}

/** GET /api/v3/inst/profiles/{holder} → {status, profile: InstProfileDetail} */
export interface InstProfileDetail extends InstProfileRow {
  dims: InstProfileDim[];
  episodes: InstEpisode[];
}

/** GET /api/v3/inst/signals → {status, signals: InstSignal[]} */
export interface InstSignal {
  holder: string;
  stock: string;
  open_date: string; // YYYYMMDD
  open_notice: string | null; // YYYYMMDD
  holder_type: string | null;
  sw_l1_at_open: string | null;
  n_adds: number;
  holder_n_closed: number;
  holder_median_alpha: number | null;
  holder_win_rate: number | null;
}

export type ProfileOrderBy = "median_alpha" | "win_rate_alpha" | "n_closed" | "avg_alpha";

// ── C2 实盘模拟 ────────────────────────────────────────────────────────────

/** GET /api/v3/paper/portfolio → positions[] (routers zip 列名; entry_date 为 ISO YYYY-MM-DD) */
export interface PaperPosition {
  position_id: string;
  strategy_tag: string;
  stock_code: string;
  shares: number;
  entry_date: string;
  entry_price: number;
  status: "open" | "closed";
  exit_date: string | null;
  exit_price: number | null;
  note: string | null;
}

/** GET /api/v3/paper/portfolio → kpi (nav/ret_cum/bench_ret_cum/excess_cum 仅有 nav 快照后出现) */
export interface PaperKpi {
  init_cash: number;
  n_closed: number;
  win_rate: number | null;
  nav?: number;
  ret_cum?: number;
  bench_ret_cum?: number;
  excess_cum?: number;
}

/** GET /api/v3/paper/nav → nav[] */
export interface PaperNavPoint {
  nav_date: string; // ISO YYYY-MM-DD
  nav: number;
  cash: number;
  position_value: number;
  n_open: number;
  bench_close: number | null;
}

/** POST /api/v3/paper/positions 请求体 (routers AddPositionReq: amount|shares 二选一) */
export interface AddPositionReq {
  stock_code: string; // 6 位
  amount?: number;
  shares?: number;
  strategy_tag?: string;
  note?: string;
}

/** POST /api/v3/paper/positions → data (services.add_position 返回, 读代码) */
export interface AddPositionResp {
  position_id: string;
  stock_code: string;
  shares: number;
  entry_date: string;
  entry_price: number;
  fee: number;
}

/** DELETE /api/v3/paper/positions/{id} → data (services.close_position 返回, 读代码) */
export interface ClosePositionResp {
  position_id: string;
  stock_code: string;
  exit_date: string;
  exit_price: number;
  pnl: number;
  ret_pct: number;
}

/** POST /api/v3/paper/mark → data (services.mark_to_market 返回, 读代码) */
export interface MarkResp {
  nav_date: string;
  nav: number;
  cash: number;
  position_value: number;
  n_open: number;
}
