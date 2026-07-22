/** Cap A moneyflow decision-assist API — Tier3/product (not pulse sensing). */
import { apiGet } from "./client";

export type AssistChain = "dc_industry" | "dc_concept" | "sw_industry";
export type AssistBehavior = "latent" | "chase" | "distribute" | "unknown";

export const ASSIST_HORIZONS = [1, 3, 5, 10, 20, 30, 60] as const;
export type AssistHorizon = (typeof ASSIST_HORIZONS)[number];

export interface AssistHorizonMetrics {
  horizon: number;
  status: "known" | "unknown";
  coverage_days: number;
  required_days?: number;
  cum_net: number | null;
  relative_ratio_pct: number | null;
  window_return_pct: number | null;
  ratio_source?: string;
}

export interface AssistBehaviorBlock {
  behavior: AssistBehavior;
  behavior_zh: string;
  flow_regime: string | null;
  version: string;
  window_return_pct: number | null;
}

export interface MoneyflowBoardRow {
  sector_code: string;
  sector_name: string | null;
  level: string | null;
  trade_date: string | null;
  flow_regime: string | null;
  cum_ratio_20d: number | null;
  horizon: AssistHorizonMetrics;
  behavior: AssistBehaviorBlock;
  conclusion: string | null;
}

export interface MoneyflowBoardResp {
  status: string;
  surface: string;
  behavior_version: string;
  disclaimer: string;
  chain: AssistChain;
  level: string | null;
  as_of: string | null;
  horizon: number;
  horizons: number[];
  count: number;
  rows: MoneyflowBoardRow[];
}

export function fetchMoneyflowBoard(opts: {
  chain?: AssistChain;
  horizon?: AssistHorizon | number;
  level?: "L1" | "L2" | "L3";
  limit?: number;
}): Promise<MoneyflowBoardResp> {
  const q = new URLSearchParams();
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.horizon !== undefined) q.set("horizon", String(opts.horizon));
  if (opts.level) q.set("level", opts.level);
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<MoneyflowBoardResp>(`/api/v3/decision/moneyflow/board${qs ? `?${qs}` : ""}`);
}

export interface StockMoneyflowResp {
  status: string;
  surface: string;
  behavior_version: string;
  disclaimer: string;
  stock_code: string;
  circ_mv: {
    value: number | null;
    as_of: string | null;
    unit: string;
    note: string;
  };
  planes: {
    moneyflow_dc: {
      label: string;
      vendor: string;
      status: string;
      as_of: string | null;
      horizons: AssistHorizonMetrics[];
    };
    moneyflow_tushare: {
      label: string;
      vendor: string;
      status: string;
      as_of: string | null;
      horizons: AssistHorizonMetrics[];
    };
  };
  sector_context: {
    chain: string;
    sector_code: string;
    sector_name: string | null;
    trade_date?: string | null;
    flow_regime?: string | null;
    behavior?: AssistBehaviorBlock;
    conclusion?: string | null;
    status: string;
  } | null;
  behavior: AssistBehaviorBlock;
  conclusion: string | null;
  /** Signed consecutive same-sign DC net days (+in / -out); CX-3 serve brick. */
  flow_streak?: number | null;
  flow_streak_direction?: "inflow" | "outflow" | null;
  flow_streak_as_of?: string | null;
  horizons: number[];
  gaps: string[];
}

export function fetchStockMoneyflow(code: string): Promise<StockMoneyflowResp> {
  return apiGet<StockMoneyflowResp>(
    `/api/v3/decision/moneyflow/stock/${encodeURIComponent(code)}`,
  );
}

// ── Cap 4D 交集最强股 ───────────────────────────────────────────────────────

export interface IntersectionSectorRef {
  sector_code: string;
  sector_name: string | null;
  behavior: AssistBehavior;
  behavior_zh: string;
}

export interface IntersectionRow {
  stock_code: string;
  ts_code: string;
  stock_name: string | null;
  industry_sectors: IntersectionSectorRef[];
  concept_sectors: IntersectionSectorRef[];
  sw_sectors: IntersectionSectorRef[];
  why: string;
}

export interface IntersectionAsOf {
  dc_industry: string | null;
  dc_concept: string | null;
  sw_industry: string | null;
}

export interface IntersectionBoardResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  as_of: IntersectionAsOf;
  horizon: number;
  horizons: number[];
  count: number;
  rows: IntersectionRow[];
  chains?: string[];
  strong_sector_counts?: {
    dc_industry: number;
    dc_concept: number;
    sw_industry: number;
  };
}

export interface IntersectionStockResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  stock_code: string;
  horizon: number;
  horizons: number[];
  as_of: IntersectionAsOf;
  in_intersection: boolean;
  detail: IntersectionRow | null;
}

export function fetchIntersectionStrongest(opts: {
  horizon?: AssistHorizon | number;
  limit?: number;
}): Promise<IntersectionBoardResp> {
  const q = new URLSearchParams();
  if (opts.horizon !== undefined) q.set("horizon", String(opts.horizon));
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<IntersectionBoardResp>(
    `/api/v3/decision/intersection/strongest${qs ? `?${qs}` : ""}`,
  );
}

export function fetchStockIntersection(
  code: string,
  horizon: AssistHorizon | number = 20,
): Promise<IntersectionStockResp> {
  return apiGet<IntersectionStockResp>(
    `/api/v3/decision/intersection/stock/${encodeURIComponent(code)}?horizon=${horizon}`,
  );
}

// ── CX-3: briefing + facet serve bricks ─────────────────────────────────────

export type BriefingTrust = "trusted" | "untrusted";

export interface BriefingInputTrust {
  trust: BriefingTrust;
  reason: string | null;
  as_of: string | null | Record<string, string | null>;
  count?: number | null;
  status?: string | null;
  chain?: string | null;
}

export interface BriefingSectionItem {
  kind: string;
  text: string;
  sector_code?: string;
  sector_name?: string | null;
  behavior?: string;
  stock_code?: string;
  stock_name?: string | null;
}

export interface BriefingSection {
  id: string;
  title: string;
  source?: string;
  count?: number;
  items: BriefingSectionItem[];
}

export interface DailyBriefingResp {
  status: "ok" | "stale" | "unavailable";
  surface: string;
  surface_version: string;
  disclaimer: string;
  horizon: number;
  as_of: string | null;
  inputs: {
    moneyflow: BriefingInputTrust;
    intersection: BriefingInputTrust;
    screener: BriefingInputTrust;
  };
  sections: BriefingSection[];
  narrative: string | null;
  reason: string | null;
  tier0_write: boolean;
}

export function fetchDailyBriefing(horizon: AssistHorizon | number = 20): Promise<DailyBriefingResp> {
  return apiGet<DailyBriefingResp>(`/api/v3/decision/briefing/daily?horizon=${horizon}`);
}

export interface SectorMembershipRow {
  stock_code: string;
  ts_code: string;
  stock_name: string | null;
}

export interface SectorMembershipResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  chain: AssistChain;
  sector_code: string;
  as_of: string | null;
  membership_pit: boolean;
  honesty_note?: string;
  count: number;
  rows: SectorMembershipRow[];
}

export function fetchSectorMembership(opts: {
  sector_code: string;
  chain?: AssistChain;
}): Promise<SectorMembershipResp> {
  const q = new URLSearchParams();
  q.set("sector_code", opts.sector_code);
  if (opts.chain) q.set("chain", opts.chain);
  return apiGet<SectorMembershipResp>(`/api/v3/decision/sector/members?${q.toString()}`);
}

export type FlowStreakDirection = "inflow" | "outflow";

export interface FlowStreakRow {
  stock_code: string;
  stock_name: string | null;
  flow_streak: number;
  direction: FlowStreakDirection;
  streak_days: number;
  as_of: string;
  cum_net_vendor: number | null;
  cum_net_unit: string;
  why: string;
}

export interface FlowStreakUniverseResp {
  status: "ok" | "stale";
  reason: string | null;
  surface: string;
  surface_version: string;
  disclaimer: string;
  vendor: string;
  direction: FlowStreakDirection;
  min_streak: number;
  as_of: string | null;
  count: number;
  rows: FlowStreakRow[];
}

export function fetchFlowStreakUniverse(opts: {
  direction?: FlowStreakDirection;
  min_streak?: number;
  limit?: number;
}): Promise<FlowStreakUniverseResp> {
  const q = new URLSearchParams();
  if (opts.direction) q.set("direction", opts.direction);
  if (opts.min_streak !== undefined) q.set("min_streak", String(opts.min_streak));
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<FlowStreakUniverseResp>(
    `/api/v3/decision/moneyflow/stock_streak${qs ? `?${qs}` : ""}`,
  );
}
