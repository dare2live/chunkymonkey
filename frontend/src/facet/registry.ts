/** Facet registry — every shown computed facet is a jump target.
 *  Jumps consume shipped serve bricks (Cap A/B/D/F); no browser-side recompute. */

export type FacetKind =
  | "behavior"
  | "form_name"
  | "axis_pos"
  | "axis_trend"
  | "axis_purity"
  | "axis_vol"
  | "breakout"
  | "intersection"
  | "holder";

export type FacetJumpStatus = "live" | "stub";

export interface FacetRef {
  kind: FacetKind;
  value: string;
  label: string;
  /** Optional horizon for moneyflow board (default 20). */
  horizon?: number;
  /** Origin stock/sector for back-link. */
  from?: string;
}

const AXIS_ZH: Record<string, Record<string, string>> = {
  axis_pos: { low: "低位", mid: "中位", high: "高位" },
  axis_trend: { up: "上行", down: "下行", flat: "横盘" },
  axis_purity: { trending: "结构干净", choppy: "结构嘈杂" },
  axis_vol: { heavy: "放量", shrink: "缩量", normal: "常量" },
};

const BEHAVIOR_ZH: Record<string, string> = {
  latent: "潜伏",
  chase: "抢筹",
  distribute: "出货",
  unknown: "未形成结论",
};

export function facetStatus(kind: FacetKind): FacetJumpStatus {
  switch (kind) {
    case "behavior":
    case "form_name":
    case "axis_pos":
    case "axis_trend":
    case "axis_purity":
    case "axis_vol":
    case "breakout":
    case "intersection":
    case "holder":
      return "live";
    default:
      return "stub";
  }
}

export function facetExplorePath(ref: FacetRef): string {
  if (ref.kind === "holder") {
    return `/institutions/${encodeURIComponent(ref.value)}`;
  }
  const q = new URLSearchParams();
  q.set("kind", ref.kind);
  q.set("value", ref.value);
  if (ref.horizon != null) q.set("horizon", String(ref.horizon));
  if (ref.from) q.set("from", ref.from);
  return `/explore?${q.toString()}`;
}

export function marketDeepLink(opts: {
  tab: "assist" | "screener" | "intersection" | "sensing";
  behavior?: string;
  formName?: string;
  axisPos?: string;
  axisTrend?: string;
  axisPurity?: string;
  axisVol?: string;
  breakout?: boolean;
}): string {
  const q = new URLSearchParams();
  q.set("tab", opts.tab);
  if (opts.behavior) q.set("behavior", opts.behavior);
  if (opts.formName) q.set("form_name", opts.formName);
  if (opts.axisPos) q.set("axis_pos", opts.axisPos);
  if (opts.axisTrend) q.set("axis_trend", opts.axisTrend);
  if (opts.axisPurity) q.set("axis_purity", opts.axisPurity);
  if (opts.axisVol) q.set("axis_vol", opts.axisVol);
  if (opts.breakout) q.set("breakout", "1");
  return `/market?${q.toString()}`;
}

export function behaviorLabel(value: string): string {
  return BEHAVIOR_ZH[value] ?? value;
}

export function axisLabel(kind: FacetKind, value: string): string {
  return AXIS_ZH[kind]?.[value] ?? value;
}

/** Build L1 chip set from dossier + moneyflow bricks already on screen. */
export function chipsFromDossier(opts: {
  stockCode: string;
  formName?: string | null;
  axisPos?: string | null;
  axisTrend?: string | null;
  axisPurity?: string | null;
  axisVol?: string | null;
  breakout?: boolean | null;
  behavior?: string | null;
  behaviorZh?: string | null;
  inIntersection?: boolean;
}): FacetRef[] {
  const chips: FacetRef[] = [];
  const from = opts.stockCode;
  if (opts.behavior && opts.behavior !== "unknown") {
    chips.push({
      kind: "behavior",
      value: opts.behavior,
      label: opts.behaviorZh ?? behaviorLabel(opts.behavior),
      horizon: 20,
      from,
    });
  }
  if (opts.formName) {
    chips.push({
      kind: "form_name",
      value: opts.formName,
      label: opts.formName,
      from,
    });
  }
  for (const [kind, val] of [
    ["axis_pos", opts.axisPos],
    ["axis_trend", opts.axisTrend],
    ["axis_purity", opts.axisPurity],
    ["axis_vol", opts.axisVol],
  ] as const) {
    if (val) {
      chips.push({
        kind,
        value: val,
        label: axisLabel(kind, val),
        from,
      });
    }
  }
  if (opts.breakout) {
    chips.push({ kind: "breakout", value: "1", label: "突破日", from });
  }
  if (opts.inIntersection) {
    chips.push({ kind: "intersection", value: "1", label: "三链交集", from });
  }
  return chips;
}
