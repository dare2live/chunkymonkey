/** C4 市场感知 API — 字段契约 = backend/routers/market_pulse.py 返回 (列名 zip, 2026-07-02 v2 / 2026-07-03 v3)。
 *  单位口径: rs_* 已是百分点 (显示不再 ×100); pct_change 是百分数 (2.5 = +2.5%);
 *  zha_ban_rate 是 0-1 比率; net_amount 引擎已归一到元 (本层不做单位换算 — measured not estimated)。
 *  v3 注意: net_amount 两链口径不可比 (dc=东财主力口径 / sw=tushare 全单口径聚合), 禁跨链排同一榜。 */
import { apiGet } from "./client";

export type PulseChain = "dc_concept" | "sw_industry";

/** 申万层级 (sw 链; dc 链 level 为东财透出值或 null)。 */
export type SwLevel = "L1" | "L2" | "L3";

/** 资金流形态标签 (v3.1 分类学, 引擎判定序第一命中; key 入库英文, 中文显示名前端映射)。 */
export type FlowRegime =
  | "surge_in"
  | "accum_in_silent"
  | "accum_in_driving"
  | "surge_out"
  | "accum_out_silent"
  | "accum_out_driving"
  | "neutral";

// ── GET /api/v3/pulse/heatmap ──────────────────────────────────────────────

export interface HeatmapSector {
  sector_code: string;
  sector_name: string;
  total_net_amount: number | null;
  /** 与 dates 等长对齐; 缺日 = null */
  values: (number | null)[];
}

/** dc 链板块类型 (v2 缺口①): 后端白名单 = market_pulse.yaml dc_content_types。 */
export type DcContentType = "行业" | "概念";

export interface HeatmapResp {
  status: string;
  chain: PulseChain;
  content_type: string;
  dates: string[]; // YYYYMMDD 升序
  sectors: HeatmapSector[]; // 窗口累计 net_amount 降序, top N
}

export function fetchHeatmap(
  opts: {
    chain?: PulseChain;
    content_type?: DcContentType;
    level?: SwLevel; // v3: 仅 sw 链生效, 默认 L1 (后端契约)
    days?: number;
    top?: number;
  } = {},
) {
  const q = new URLSearchParams();
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.content_type) q.set("content_type", opts.content_type);
  if (opts.level) q.set("level", opts.level);
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
  chain: PulseChain;
  level?: SwLevel; // v3 (sw 链): 本次列表的层级
  latest_date: string | null;
  prev_date: string | null;
  sectors: RotationSector[]; // 最新日 rs_rank_4w 升序 (v3: 同级分区排名)
}

export function fetchRotation(opts: { lag?: number; level?: SwLevel } = {}) {
  const q = new URLSearchParams();
  if (opts.lag !== undefined) q.set("lag", String(opts.lag));
  if (opts.level) q.set("level", opts.level);
  const qs = q.toString();
  return apiGet<RotationResp>(`/api/v3/pulse/rotation${qs ? `?${qs}` : ""}`);
}

/** v2 dc 资金流轮动行: rank_flow = dc 全体 (行业+概念) 截面资金流排名 (1=流入最强);
 *  leading/leading_pct = dc_index 涨幅龙头; flow_leader_stock = 资金龙头 (moneyflow_ind_dc);
 *  inflow_breadth = 当日有个股流数据的成分股中净流入占比 0-1 (dc_member 快照窗内才有, 缺=null)。 */
export interface DcRotationSector {
  sector_code: string;
  sector_name: string;
  content_type: string | null;
  pct_change: number | null;
  net_amount: number | null;
  rank_flow: number | null;
  prev_rank_flow: number | null;
  inflow_breadth: number | null;
  leading: string | null;
  leading_pct: number | null;
  flow_leader_stock: string | null;
}

export interface DcRotationResp {
  status: string;
  chain: PulseChain;
  latest_date: string | null;
  prev_date: string | null;
  sectors: DcRotationSector[]; // 最新日 rank_flow 升序, top 截断
}

export function fetchDcRotation(opts: { lag?: number; top?: number } = {}) {
  const q = new URLSearchParams({ chain: "dc_concept" });
  if (opts.lag !== undefined) q.set("lag", String(opts.lag));
  if (opts.top !== undefined) q.set("top", String(opts.top));
  return apiGet<DcRotationResp>(`/api/v3/pulse/rotation?${q.toString()}`);
}

// ── GET /api/v3/pulse/flow_board (v3, 替代 v1 /quiet) ──────────────────────

/** 资金流向榜行: flow_regime 非 neutral 的板块 + 量级 (近 cum_window 日累计) + mini 条纹。 */
export interface FlowBoardRow {
  chain: PulseChain;
  sector_code: string;
  sector_name: string;
  level: string | null;
  content_type: string | null;
  trade_date: string;
  pct_change: number | null;
  net_amount: number | null; // 当日净流入 (元; 两链口径不可比)
  flow_z: number | null; // 突然性 z-score (窗口不满 = null)
  flow_streak: number | null; // 带符号连续净流向天数 (+流入 / -流出)
  cum_ratio_20d: number | null; // 近20日累计净流 / 板块市值 (%); 满窗才有
  flow_regime: FlowRegime;
  cum_net: number | null; // 近 cum_window 日累计净额 (元)
  stripe: (number | null)[]; // 与 stripe_dates 对齐的逐日净流序列
}

export interface FlowBoardResp {
  status: string;
  chain: PulseChain;
  trade_date: string | null;
  stripe_dates: string[]; // YYYYMMDD 升序
  inflow: FlowBoardRow[]; // 流入形态组 (cum_ratio 降序, NULL 沉底)
  outflow: FlowBoardRow[]; // 流出形态组 (镜像)
}

export function fetchFlowBoard(
  opts: { chain?: PulseChain; level?: SwLevel; limit?: number; stripe_days?: number } = {},
) {
  const q = new URLSearchParams();
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.level) q.set("level", opts.level);
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  if (opts.stripe_days !== undefined) q.set("stripe_days", String(opts.stripe_days));
  const qs = q.toString();
  return apiGet<FlowBoardResp>(`/api/v3/pulse/flow_board${qs ? `?${qs}` : ""}`);
}

// ── GET /api/v3/pulse/flow_stripe (v3 mini 温度条纹) ───────────────────────

export interface FlowStripeResp {
  status: string;
  chain: PulseChain;
  sector_code: string;
  sector_name: string | null;
  dates: string[]; // 升序
  values: (number | null)[]; // 逐日净流入 (元)
}

export function fetchFlowStripe(opts: { code: string; chain?: PulseChain; days?: number }) {
  const q = new URLSearchParams({ code: opts.code });
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.days !== undefined) q.set("days", String(opts.days));
  return apiGet<FlowStripeResp>(`/api/v3/pulse/flow_stripe?${q.toString()}`);
}

// ── GET /api/v3/pulse/drill (v3 统一层级下钻) ──────────────────────────────

export interface DrillCrumb {
  code: string;
  name: string | null;
  level: string; // 'L1'|'L2'|'L3'|'sector'
}

/** 板块层下钻行 (mart 直读; 不适用列 null)。 */
export interface DrillSectorRow {
  sector_code: string;
  sector_name: string | null;
  level: string | null;
  content_type: string | null;
  trade_date: string | null;
  pct_change: number | null;
  net_amount: number | null;
  rank_flow: number | null;
  rs_4w: number | null;
  rs_12w: number | null;
  rs_rank_4w: number | null;
  flow_z: number | null;
  flow_streak: number | null;
  cum_ratio_20d: number | null;
  flow_regime: FlowRegime | null;
}

/** 成分股叶子行 (选股落点): 近20日净流 + 实时资金形态 + B2 形态 + 连板。 */
export interface DrillStockRow {
  ts_code: string;
  stock_code: string;
  name: string | null;
  trade_date: string | null; // 该股最新流数据日
  net_amount: number | null;
  cum_net: number | null;
  flow_z: number | null;
  flow_streak: number | null;
  flow_regime: FlowRegime | null;
  form_name: string | null;
  is_breakout_event: boolean | null;
  limit_times: number | null;
  pct_change: number | null;
}

export type DrillResp = {
  status: string;
  chain: PulseChain;
  date: string | null;
  breadcrumb: DrillCrumb[];
  member_as_of?: string | null;
} & (
  | { rows_level: "stock"; rows: DrillStockRow[] }
  | { rows_level: "L1" | "L2" | "L3" | "sector" | null; rows: DrillSectorRow[] }
);

export function fetchDrill(opts: {
  chain: PulseChain;
  code?: string | null;
  date?: string;
  content_type?: DcContentType;
  top?: number;
}) {
  const q = new URLSearchParams({ chain: opts.chain });
  if (opts.code) q.set("code", opts.code);
  if (opts.date) q.set("date", opts.date);
  if (opts.content_type) q.set("content_type", opts.content_type);
  if (opts.top !== undefined) q.set("top", String(opts.top));
  return apiGet<DrillResp>(`/api/v3/pulse/drill?${q.toString()}`);
}

// ── GET /api/v3/pulse/sentiment ────────────────────────────────────────────

export interface SentimentPoint {
  trade_date: string;
  mkt_net_amount: number | null;
  limit_up_total: number | null;
  limit_down_total: number | null;
  zha_ban_rate: number | null; // Z/(U+Z), 0-1; 源缺日 = null (不知道≠0)
  adv_dec_ratio: number | null;
  // ── v2 情绪周期 (limit_list_d 族; 口径契约: 官方不含 ST) ──
  max_limit_times: number | null; // 当日最高连板
  limit_times_dist_json: string | null; // n板家数 {"1":x,"2":y}; '{}'=源在场真无涨停, null=源缺日
  promotion_rate: number | null; // 晋级率 0-1: 今日>=2板 ÷ 昨日>=1板; 昨日 0 板 = null
  sec_board_n: number | null; // 秒板数 (first_time <= 09:30:59 的 U)
  avg_fd_amount: number | null; // U 行封单均额 (元)
  open_times_total: number | null; // 炸板总次数 (U+Z 行 open_times 和)
  // ── v2 水位 ──
  rzrqye: number | null; // 两融余额 (元, 跨交易所直和; t+1 披露行日期=余额日)
  rzrqye_chg: number | null; // 两融余额相邻源日差 (元)
  mkt_pe: number | null; // 大盘 PE (index_dailybasic.pe_ttm, mkt_valuation_code 行)
  mkt_turnover: number | null; // 大盘换手 (turnover_rate_f 自由流通口径, %)
  lhb_count: number | null; // 龙虎榜上榜家数 (DISTINCT 股)
  lhb_inst_net: number | null; // 龙虎榜披露席位净买直和 (元; 源含游资营业部席位非纯机构)
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

// ── GET /api/v3/pulse/strongest ────────────────────────────────────────────

/** limit_cpt_list 最强板块行 (885xxx.TI 同花顺码 — 独立卡, 禁与 dc/sw 链拼接)。 */
export interface StrongestSector {
  ts_code: string;
  name: string;
  days: number | null; // 连续上榜天数
  up_stat: string | null; // 连板高度 (如 "3天3板")
  cons_nums: string | null; // 连板家数 (源为字符串)
  up_nums: number | null; // 涨停家数
  pct_chg: number | null; // 板块涨跌幅 (百分数)
  rank: number | null; // 板块热点排名 (1=最强)
}

export interface StrongestResp {
  status: string;
  trade_date: string | null; // 最近一个有榜的入库日 (冰点日源端无榜合法)
  sectors: StrongestSector[]; // rank 升序
}

export function fetchStrongest() {
  return apiGet<StrongestResp>("/api/v3/pulse/strongest");
}

// ── GET /api/v3/pulse/members ──────────────────────────────────────────────

export interface MemberRow {
  con_code: string;
  name: string | null;
}

export interface MembersResp {
  status: string;
  chain: PulseChain;
  sector_code: string;
  as_of: string | null; // dc = 成分快照日; sw 当前成分 = null
  members: MemberRow[];
}

export function fetchMembers(opts: { sector_code: string; chain?: PulseChain }) {
  const q = new URLSearchParams({ sector_code: opts.sector_code });
  if (opts.chain) q.set("chain", opts.chain);
  return apiGet<MembersResp>(`/api/v3/pulse/members?${q.toString()}`);
}
