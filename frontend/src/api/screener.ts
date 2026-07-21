/** Cap 5B 形态/阶段选股面 API — Tier3/product filter surface (same Tier1 brick as 档案 F)。 */
import { apiGet } from "./client";

export type ScreenerAxisPos = "low" | "mid" | "high";
export type ScreenerAxisTrend = "up" | "down" | "flat";
export type ScreenerAxisPurity = "trending" | "choppy";
export type ScreenerAxisVol = "heavy" | "shrink" | "normal";

export interface ScreenerFacetOption {
  value: string;
  label: string;
  count: number;
}

export interface ScreenerOptionsResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  as_of: string | null;
  facets: {
    form_name?: ScreenerFacetOption[];
    form_sub?: ScreenerFacetOption[];
    axis_pos?: ScreenerFacetOption[];
    axis_trend?: ScreenerFacetOption[];
    axis_purity?: ScreenerFacetOption[];
    axis_vol?: ScreenerFacetOption[];
  };
}

export function fetchScreenerOptions(): Promise<ScreenerOptionsResp> {
  return apiGet<ScreenerOptionsResp>("/api/v3/screener/options");
}

export interface ScreenerRow {
  stock_code: string;
  stock_name: string | null;
  trade_date: string;
  form_name: string | null;
  form_sub: string | null;
  weekly_name: string | null;
  monthly_name: string | null;
  is_breakout_event: boolean | null;
  axis_pos: string | null;
  axis_trend: string | null;
  axis_purity: string | null;
  axis_vol: string | null;
  axis_pos_zh: string | null;
  axis_trend_zh: string | null;
  axis_purity_zh: string | null;
  axis_vol_zh: string | null;
  base_days: number | null;
  why: string;
}

export interface ScreenerFormStageResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  as_of: string | null;
  filters_applied: Record<string, unknown>;
  count: number;
  truncated?: boolean;
  rows: ScreenerRow[];
}

export function fetchScreenerFormStage(opts: {
  formNames?: string[];
  axisPos?: ScreenerAxisPos;
  axisTrend?: ScreenerAxisTrend;
  axisPurity?: ScreenerAxisPurity;
  axisVol?: ScreenerAxisVol;
  isBreakoutEvent?: boolean;
  limit?: number;
}): Promise<ScreenerFormStageResp> {
  const q = new URLSearchParams();
  for (const fn of opts.formNames ?? []) q.append("form_name", fn);
  if (opts.axisPos) q.set("axis_pos", opts.axisPos);
  if (opts.axisTrend) q.set("axis_trend", opts.axisTrend);
  if (opts.axisPurity) q.set("axis_purity", opts.axisPurity);
  if (opts.axisVol) q.set("axis_vol", opts.axisVol);
  if (opts.isBreakoutEvent !== undefined) q.set("is_breakout_event", String(opts.isBreakoutEvent));
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<ScreenerFormStageResp>(`/api/v3/screener/form_stage${qs ? `?${qs}` : ""}`);
}
