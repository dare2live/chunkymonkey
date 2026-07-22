/**
 * Cap D Enrich charts — Sankey + parallel coordinates from intersection/strongest rows.
 * No browser-side ∩ recompute; membership only. Caps lines to avoid soup.
 */
import type { EChartsOption } from "echarts";
import type { IntersectionRow } from "../api/decision";
import { UI } from "../theme";

const MAX_STOCKS = 16;
const MAX_LINKS = 80;

function shortName(name: string | null | undefined, code: string, max = 8): string {
  const s = (name || code).trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** 3-column Sankey: 行业 → 股 → 概念（申万 as secondary stock→sw links when room）. */
export function intersectionSankeyOption(rows: IntersectionRow[]): EChartsOption | null {
  const slice = rows.slice(0, MAX_STOCKS);
  if (!slice.length) return null;

  const nodes: { name: string }[] = [];
  const nodeIndex = new Map<string, number>();
  const ensure = (name: string) => {
    if (!nodeIndex.has(name)) {
      nodeIndex.set(name, nodes.length);
      nodes.push({ name });
    }
    return nodeIndex.get(name)!;
  };

  type Link = { source: string; target: string; value: number };
  const links: Link[] = [];
  const pushLink = (source: string, target: string) => {
    if (links.length >= MAX_LINKS) return;
    const existing = links.find((l) => l.source === source && l.target === target);
    if (existing) {
      existing.value += 1;
    } else {
      links.push({ source, target, value: 1 });
    }
  };

  for (const r of slice) {
    // Include code so truncated display names cannot collide across tickers.
    const stock = `股·${shortName(r.stock_name, r.stock_code, 5)}·${r.stock_code}`;
    ensure(stock);
    for (const s of r.industry_sectors.slice(0, 2)) {
      const ind = `行·${shortName(s.sector_name, s.sector_code)}`;
      ensure(ind);
      pushLink(ind, stock);
    }
    for (const s of r.concept_sectors.slice(0, 2)) {
      const con = `概·${shortName(s.sector_name, s.sector_code)}`;
      ensure(con);
      pushLink(stock, con);
    }
    for (const s of (r.sw_sectors ?? []).slice(0, 1)) {
      const sw = `申·${shortName(s.sector_name, s.sector_code)}`;
      ensure(sw);
      pushLink(stock, sw);
    }
  }

  if (!links.length) return null;

  return {
    animationDuration: 280,
    tooltip: { trigger: "item" },
    series: [
      {
        type: "sankey",
        emphasis: { focus: "adjacency" },
        data: nodes,
        links,
        nodeAlign: "justify",
        lineStyle: { color: "gradient", curveness: 0.45, opacity: 0.35 },
        label: { color: UI.textDim, fontSize: 10 },
        itemStyle: { borderWidth: 0, color: UI.accent },
      },
    ],
  };
}

/** Known behaviors only; unknown excluded from mean (never scored as 0). */
function behaviorScore(beh: string): number | null {
  switch (beh) {
    case "latent":
      return 2;
    case "chase":
      return 1;
    case "distribute":
      return -1;
    default:
      return null;
  }
}

/** Parallel axes: per-chain membership counts + mean known-behavior score. */
export function intersectionParcoordsOption(rows: IntersectionRow[]): EChartsOption | null {
  const slice = rows.slice(0, MAX_STOCKS);
  if (!slice.length) return null;

  const data = slice.map((r) => {
    const inds = r.industry_sectors;
    const cons = r.concept_sectors;
    const sws = r.sw_sectors ?? [];
    const scores = [...inds, ...cons, ...sws]
      .map((s) => behaviorScore(s.behavior))
      .filter((v): v is number => v != null);
    const meanBeh =
      scores.length > 0
        ? scores.reduce((a, v) => a + v, 0) / scores.length
        : Number.NaN; // axis shows gap — not fake 0
    return {
      value: [
        inds.length,
        cons.length,
        sws.length,
        Number.isFinite(meanBeh) ? Number(meanBeh.toFixed(2)) : null,
      ] as [number, number, number, number | null],
      name: shortName(r.stock_name, r.stock_code, 10),
      code: r.stock_code,
      why: r.why,
    };
  });

  const plotted = data.filter((d): d is typeof d & { value: [number, number, number, number] } =>
    d.value[3] != null,
  );
  if (!plotted.length) return null;

  return {
    animationDuration: 240,
    parallelAxis: [
      { dim: 0, name: "行业数", min: 0, max: Math.max(3, ...plotted.map((d) => d.value[0])) },
      { dim: 1, name: "概念数", min: 0, max: Math.max(3, ...plotted.map((d) => d.value[1])) },
      { dim: 2, name: "申万数", min: 0, max: Math.max(3, ...plotted.map((d) => d.value[2])) },
      {
        dim: 3,
        name: "行为分",
        min: -1,
        max: 2,
        nameTextStyle: { color: UI.textDim },
      },
    ],
    parallel: {
      left: 48,
      right: 28,
      top: 36,
      bottom: 24,
      parallelAxisDefault: {
        nameTextStyle: { color: UI.textDim, fontSize: 11 },
        axisLine: { lineStyle: { color: UI.border } },
        axisLabel: { color: UI.textFaint, fontSize: 10 },
      },
    },
    tooltip: {
      formatter: (p: unknown) => {
        const d = (p as { data?: (typeof data)[number] }).data;
        if (!d) return "";
        return `<b>${d.name}</b> ${d.code}<br/>${d.why || ""}`;
      },
    },
    series: [
      {
        type: "parallel",
        lineStyle: { width: 1.5, opacity: 0.55, color: UI.accent },
        data: plotted,
        emphasis: { lineStyle: { width: 2.5, opacity: 0.95 } },
      },
    ],
  };
}
