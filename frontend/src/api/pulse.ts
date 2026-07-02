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
  opts: { chain?: PulseChain; content_type?: DcContentType; days?: number; top?: number } = {},
) {
  const q = new URLSearchParams();
  if (opts.chain) q.set("chain", opts.chain);
  if (opts.content_type) q.set("content_type", opts.content_type);
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
  latest_date: string | null;
  prev_date: string | null;
  sectors: RotationSector[]; // 最新日 rs_rank_4w 升序
}

export function fetchRotation(opts: { lag?: number } = {}) {
  const qs = opts.lag !== undefined ? `?lag=${opts.lag}` : "";
  return apiGet<RotationResp>(`/api/v3/pulse/rotation${qs}`);
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
