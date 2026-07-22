/**
 * Capital Terrain 2.5D hero — Enrich context band above the latent quadrant.
 * Height = relative_ratio_pct; hue = window_return_pct (cool=flat price / warm=surged).
 * Latent signature = tall cool tower. Kill: if occlusion hides values, collapse to legend-only.
 * No echarts-gl; plain CSS isometric extrusion. unknown/thin never painted as 0.
 */
import { useMemo, type CSSProperties } from "react";
import type { MoneyflowBoardRow } from "../api/decision";

const MAX_TOWERS = 28;

function returnHue(ret: number, maxAbs: number): string {
  const t = maxAbs > 0 ? Math.min(1, Math.abs(ret) / maxAbs) : 0;
  if (ret >= 0) {
    // warm ember (抢筹) — restrained A-share red
    const a = 0.18 + t * 0.55;
    return `rgba(212, 52, 44, ${a.toFixed(3)})`;
  }
  // cool slate-blue (潜伏 / flat-to-down) — not AI teal
  const a = 0.2 + t * 0.45;
  return `rgba(91, 122, 157, ${a.toFixed(3)})`;
}

export function CapitalTerrain(props: {
  rows: MoneyflowBoardRow[];
  selected?: string | null;
  onSelect?: (sectorCode: string, name: string | null) => void;
}) {
  const towers = useMemo(() => {
    const known = props.rows.filter(
      (r) =>
        r.horizon.status === "known" &&
        r.horizon.relative_ratio_pct != null &&
        r.horizon.window_return_pct != null,
    );
    const ranked = [...known].sort(
      (a, b) =>
        Math.abs(b.horizon.relative_ratio_pct!) - Math.abs(a.horizon.relative_ratio_pct!),
    );
    const top = ranked.slice(0, MAX_TOWERS);
    const maxH = Math.max(
      ...top.map((r) => Math.abs(r.horizon.relative_ratio_pct!)),
      0.01,
    );
    const maxRet = Math.max(
      ...top.map((r) => Math.abs(r.horizon.window_return_pct!)),
      0.01,
    );
    return top.map((r) => {
      const ratio = r.horizon.relative_ratio_pct!;
      const ret = r.horizon.window_return_pct!;
      const hPct = Math.max(8, Math.round((Math.abs(ratio) / maxH) * 100));
      const latent = ratio > 0 && ret <= 0;
      return {
        code: r.sector_code,
        name: r.sector_name,
        ratio,
        ret,
        hPct,
        color: returnHue(ret, maxRet),
        latent,
        behaviorZh: r.behavior.behavior_zh,
        conclusion: r.conclusion,
      };
    });
  }, [props.rows]);

  if (towers.length === 0) {
    return (
      <div className="terrain-empty state-hint">
        地形无已知窗行 — 不画假高度（窗未满/未知不按 0）。
      </div>
    );
  }

  return (
    <div className="terrain-hero">
      <div className="terrain-caption">
        <span className="terrain-title">潜伏地形</span>
        <span className="muted">
          高=相对净流入 · 色=窗口涨跌（冷高=潜伏）· 点塔聚焦象限
        </span>
      </div>
      <div className="terrain-field" role="list">
        {towers.map((t) => (
          <button
            key={t.code}
            type="button"
            role="listitem"
            className={`terrain-tower${props.selected === t.code ? " selected" : ""}${
              t.latent ? " latent" : ""
            }`}
            style={
              {
                "--tower-h": `${t.hPct}%`,
                "--tower-fill": t.color,
              } as CSSProperties
            }
            title={`${t.name ?? t.code}\n相对流入 ${t.ratio.toFixed(2)}% · 涨跌 ${t.ret.toFixed(2)}%\n${t.behaviorZh}${
              t.conclusion ? `\n${t.conclusion}` : ""
            }`}
            onClick={() => props.onSelect?.(t.code, t.name)}
          >
            <span className="terrain-column">
              <span className="terrain-side" />
              <span className="terrain-face" />
              <span className="terrain-top" />
            </span>
            <span className="terrain-label">{t.name ?? t.code}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
