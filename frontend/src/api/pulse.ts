/** C4 市场感知 API — 字段契约 = backend/routers/market_pulse.py 返回 (列名 zip, 2026-07-02)。
 *  单位口径: rs_* 已是百分点 (显示不再 ×100); pct_change 是百分数 (2.5 = +2.5%);
 *  zha_ban_rate 是 0-1 比率; net_amount 为源字段原值 (本层不做单位换算 — measured not estimated)。 */
import { apiGet } from "./client";

export type PulseChain = "dc_concept" | "sw_industry";

// ── GET /api/v3/pulse/heatmap ──────────────────────────────────────────────

export interface HeatmapSector {
  sector_code: string;
  sector_name: string;
  total_net_amount: number | null;
  /** 与 dates 等长对齐; 缺日 = null */
  values: (number | null)[];
}

export interface HeatmapResp {
  status: string;
  chain: PulseChain;
  dates: string[]; // YYYYMMDD 升序
  sectors: HeatmapSector[]; // 窗口累计 net_amount 降序, top N
}

export function fetchHeatmap(opts: { chain?: PulseChain; days?: number; top?: number } = {}) {
  const q = new URLSearchParams();
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.days !== undefined) q.set("days", String(opts.days));
  if (opts.top !== undefined) q.set("top", String(opts.top));
  const qs = q.toString();
  return apiGet<HeatmapResp>(`/api/v3/pulse/heatmap${qs ? `?${qs}` : ""}`);
}

// ── GET /api/v3/pulse/rotation ─────────────────────────────────────────────

export interface RotationSector {
  sector_code: string;
  sector_name: string;
  rs_4w: number | null;
  rs_12w: number | null;
  rs_rank_4w: number | null;
  prev_rs_4w: number | null;
  prev_rs_12w: number | null;
  prev_rs_rank_4w: number | null;
}

export interface RotationResp {
  status: string;
  latest_date: string | null;
  prev_date: string | null;
  sectors: RotationSector[]; // 最新日 rs_rank_4w 升序
}

export function fetchRotation(opts: { lag?: number } = {}) {
  const qs = opts.lag !== undefined ? `?lag=${opts.lag}` : "";
  return apiGet<RotationResp>(`/api/v3/pulse/rotation${qs}`);
}

// ── GET /api/v3/pulse/quiet ────────────────────────────────────────────────

export interface QuietRow {
  chain: PulseChain;
  sector_code: string;
  sector_name: string;
  trade_date: string;
  pct_change: number | null;
  net_amount: number | null;
  quiet_inflow_days: number | null;
  quiet_outflow_days: number | null;
}

export interface QuietResp {
  status: string;
  inflow: QuietRow[]; // quiet_inflow_days 降序
  outflow: QuietRow[]; // quiet_outflow_days 降序
}

export function fetchQuiet(opts: { limit?: number } = {}) {
  const qs = opts.limit !== undefined ? `?limit=${opts.limit}` : "";
  return apiGet<QuietResp>(`/api/v3/pulse/quiet${qs}`);
}

// ── GET /api/v3/pulse/sentiment ────────────────────────────────────────────

export interface SentimentPoint {
  trade_date: string;
  mkt_net_amount: number | null;
  limit_up_total: number | null;
  limit_down_total: number | null;
  zha_ban_rate: number | null; // Z/(U+Z), 0-1; 源缺日 = null (不知道≠0)
  adv_dec_ratio: number | null;
}

export interface SentimentResp {
  status: string;
  days: SentimentPoint[]; // 升序
}

export function fetchSentiment(opts: { days?: number } = {}) {
  const qs = opts.days !== undefined ? `?days=${opts.days}` : "";
  return apiGet<SentimentResp>(`/api/v3/pulse/sentiment${qs}`);
}

// ── GET /api/v3/pulse/warnings ─────────────────────────────────────────────

export interface RankDropout {
  sector_code: string;
  sector_name: string;
  prev_date: string;
  latest_date: string;
  prev_rank: number;
  latest_rank: number;
  rs_4w: number | null;
}

export interface QuietOutflowWarning {
  sector_code: string;
  sector_name: string;
  trade_date: string;
  quiet_outflow_days: number;
  net_amount: number | null;
  pct_change: number | null;
}

export interface WarningsResp {
  status: string;
  thresholds: { rank_top: number; quiet_outflow_days: number };
  rank_dropouts: RankDropout[];
  quiet_outflows: QuietOutflowWarning[];
}

export function fetchWarnings() {
  return apiGet<WarningsResp>("/api/v3/pulse/warnings");
}
