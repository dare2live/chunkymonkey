/** Market 页纯函数格式化器 / 数值工具 / 调色板 — 从 MarketPage god-component 抽出 (刃3 结构拆分)。
 *  无 JSX、无副作用、可单测。诚实门: 缺失一律 "—"/灰, 绝不用 0 糊弄 (goal.md)。 */
import { UI } from "../../theme";

/** rs_* 已是百分点 → 直接带符号显示, 不再 ×100。 */
export function fmtPts(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(1)}`;
}

/** pct_change 源值即百分数 (2.5 = +2.5%)。 */
export function fmtPctRaw(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(2)}%`;
}

/** 金额自适应中文单位 (源值=元 → 万/亿), 缺失 "—" (不用 0 糊弄)。 */
export function fmtAmountCn(x: number | null | undefined, signed = false): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = signed && x > 0 ? "+" : "";
  const a = Math.abs(x);
  if (a >= 1e8) return `${sign}${(x / 1e8).toFixed(2)}亿`;
  if (a >= 1e4) return `${sign}${(x / 1e4).toFixed(0)}万`;
  return `${sign}${x.toFixed(0)}`;
}

/** 连板天梯 JSON ({"1":50,"2":10}) → 升序 [板数, 家数]; null (源缺日) / 解析失败 → []。 */
export function parseLadder(j: string | null): [string, number][] {
  if (!j) return [];
  try {
    const o = JSON.parse(j) as Record<string, number>;
    return Object.entries(o).sort((a, b) => Number(a[0]) - Number(b[0]));
  } catch {
    return [];
  }
}

/** YYYYMMDD → MM-DD (轴标签)。 */
export const fmtMD = (d: string) => `${d.slice(4, 6)}-${d.slice(6, 8)}`;

/** 涨跌语义 class (A股红涨绿跌); 0/缺失无色。 */
export function signClass(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x) || x === 0) return "";
  return x > 0 ? "pos" : "neg";
}

/** p95 分位封顶 (防单日极值压扁全带的色阶动态范围)。 */
export function p95(vals: number[]): number {
  if (!vals.length) return 1;
  const sorted = [...vals].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 1;
}

/** 白→语义色插值: v/cap 越大颜色越深; null=源缺日显灰纹。 */
export function stripeColor(v: number | null, cap: number, rgb: string): string {
  if (v === null) return "var(--bg-panel-2)";
  const t = Math.min(1, v / cap);
  return `rgba(${rgb}, ${(t * 0.92).toFixed(3)})`;
}

/** 缺日(null)按0处理后前缀和 — 窗口内累计净流, 表达持续性趋势 (非绝对仓位)。 */
export function cumulativeSeries(values: (number | null)[]): number[] {
  let acc = 0;
  return values.map((v) => {
    acc += v ?? 0;
    return acc;
  });
}

// ── 调色板 (语义色与 theme.ts UI 同步) ────────────────────────────────────
/** 逐日净流入色带 rgb (红入绿出, warn=炸板率琥珀)。 */
export const STRIPE_RGB = { up: "212, 52, 44", down: "15, 138, 78", warn: "160, 106, 0" } as const;

/** 多板块对比曲线轮换色 (首色=accent 蓝)。 */
export const CURVE_PALETTE = [
  "#3b66d4", "#d4342c", "#0f8a4e", "#a06a00", "#7d5ba6", "#1f8a8c", "#c2703d", "#5c6bc0",
];

/** 资金行为点色: 潜伏=冷灰蓝 / 抢筹=涨红 / 出货=跌绿 / unknown=弱化灰 (永不画成 0)。 */
export const BEHAVIOR_DOT: Record<string, string> = {
  latent: "#5b7a9d",
  chase: UI.up,
  distribute: UI.down,
  unknown: UI.textFaint,
};
