/** 展示格式化 — 数据缺失一律显 "—", 不用 0 糊弄 (measured-not-estimated 展示面)。 */

export function fmtPct(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

export function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtInt(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return Math.round(x).toLocaleString("zh-CN");
}

/** YYYYMMDD → YYYY-MM-DD; 已是 ISO 或空值则原样/占位。 */
export function fmtDate(d: string | null | undefined): string {
  if (!d) return "—";
  if (/^\d{8}$/.test(d)) return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  return d;
}

/** 涨跌配色 class (A股约定: 红涨绿跌)。 */
export function pnlClass(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x) || x === 0) return "";
  return x > 0 ? "pos" : "neg";
}
