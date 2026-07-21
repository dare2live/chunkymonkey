import { apiGet } from "./client";

export interface StockDossierBasic {
  stock_code: string;
  stock_name: string | null;
  name_source: string | null;
  industry: {
    l1_code: string | null;
    l1_name: string | null;
    l2_code: string | null;
    l2_name: string | null;
    l3_code: string | null;
    l3_name: string | null;
    updated_at: string | null;
    source: string;
  } | null;
}

export interface StockFormStage {
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
  axis_volregime: string | null;
  axis_pos_memb: number | null;
  axis_trend_memb: number | null;
  axis_purity_memb: number | null;
  axis_vol_memb: number | null;
  base_days: number | null;
  source: string;
  resolver_note?: string;
}

export interface StockHolderRow {
  holder_rank: number | null;
  holder_name: string | null;
  holder_name_norm: string | null;
  holder_type: string | null;
  hold_ratio_float: number | null;
  change_status: string | null;
  hold_change_num: number | null;
  available_at: string | null;
  notice_date: string | null;
  shares_approx: number | null;
  approx_periods_present: number | null;
  return_pct: number | null;
  holding_cycle_days: number | null;
  has_institution_profile?: boolean;
}

export interface InstitutionProfileCoverage {
  holders_total: number;
  holders_with_profile: number;
  coverage: number | null;
  note: string;
}

export interface StockDossierResponse {
  status: string;
  surface: string;
  stock_code: string;
  basic: StockDossierBasic;
  form_stage: StockFormStage | null;
  observation: {
    version: string;
    text: string | null;
    as_of: string | null;
    status: string;
    axes_zh?: string[];
    form_name?: string | null;
  };
  holders: {
    report_date: string | null;
    prev_report_date: string | null;
    source: string | null;
    rows: StockHolderRow[];
    institution_profile?: InstitutionProfileCoverage;
    gaps: string[];
  };
  gaps: string[];
  pit_notes: string[];
}

export function fetchStockDossier(code: string): Promise<StockDossierResponse> {
  return apiGet<StockDossierResponse>(`/api/v3/stock/${encodeURIComponent(code)}/dossier`);
}
