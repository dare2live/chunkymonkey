/** Market 页 echarts option 构建器 — 纯函数, 从 MarketPage god-component 抽出 (刃3)。
 *  配色走 theme.ts UI; 缺日/unknown 诚实处理 (曲线断点/点不落 (0,0))。 */
import type { EChartsOption } from "echarts";
import type { HeatmapResp } from "../../api/pulse";
import type { MoneyflowBoardRow } from "../../api/decision";
import { UI } from "../../theme";
import { BEHAVIOR_DOT, CURVE_PALETTE, cumulativeSeries, fmtAmountCn, fmtMD } from "./format";

/** 多板块累计净流对比曲线 (topN 叠加, 单轴; 悬浮框汇总当日各线数值)。 */
export function multiSectorCurveOption(dates: string[], series: { name: string; values: (number | null)[] }[]): EChartsOption {
  const md = dates.map(fmtMD);
  return {
    grid: { left: 64, right: 16, top: 8, bottom: 50 },
    xAxis: {
      type: "category",
      data: md,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      axisLine: { lineStyle: { color: UI.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: UI.textFaint, fontSize: 11, formatter: (v: number) => fmtAmountCn(v) },
      splitLine: { lineStyle: { color: UI.borderSoft } },
    },
    legend: {
      bottom: 0,
      type: "scroll",
      textStyle: { color: UI.textDim, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
    },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) => fmtAmountCn(v as number, true),
    },
    series: series.map((s, i) => ({
      name: s.name,
      type: "line",
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 2, color: CURVE_PALETTE[i % CURVE_PALETTE.length] },
      itemStyle: { color: CURVE_PALETTE[i % CURVE_PALETTE.length] },
      data: cumulativeSeries(s.values),
    })),
  };
}

/** 单板块细节曲线: 柱=当日净流 (色随涨跌), 线=窗口累计净流 (右轴)。 */
export function sectorDrillCurveOption(dates: string[], values: (number | null)[]): EChartsOption {
  const md = dates.map(fmtMD);
  const cum = cumulativeSeries(values);
  return {
    grid: { left: 64, right: 64, top: 8, bottom: 28 },
    xAxis: {
      type: "category",
      data: md,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      axisLine: { lineStyle: { color: UI.border } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "当日净流",
        nameTextStyle: { color: UI.textFaint, fontSize: 10 },
        axisLabel: { color: UI.textFaint, fontSize: 11, formatter: (v: number) => fmtAmountCn(v) },
        splitLine: { lineStyle: { color: UI.borderSoft } },
      },
      {
        type: "value",
        name: "累计净流",
        nameTextStyle: { color: UI.textFaint, fontSize: 10 },
        axisLabel: { color: UI.textFaint, fontSize: 11, formatter: (v: number) => fmtAmountCn(v) },
        splitLine: { show: false },
      },
    ],
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const arr = params as unknown as { seriesName: string; value: number; axisValue: string }[];
        if (!arr.length) return "";
        const lines = arr.map((p) => `${p.seriesName}: ${fmtAmountCn(p.value, true)}`);
        return `${arr[0].axisValue}<br/>${lines.join("<br/>")}`;
      },
    },
    series: [
      {
        name: "当日净流",
        type: "bar",
        yAxisIndex: 0,
        data: values.map((v) => (v === null ? null : { value: v, itemStyle: { color: v >= 0 ? UI.up : UI.down } })),
      },
      {
        name: "累计净流",
        type: "line",
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { width: 2, color: UI.accent },
        itemStyle: { color: UI.accent },
        data: cum,
      },
    ],
  };
}

/** 资金热力图: 分向色阶 绿→白→红, 白=零点 (零流入自然隐没, 强流向自然跳出)。 */
export function heatmapOption(resp: HeatmapResp): EChartsOption {
  const dates = resp.dates.map(fmtMD);
  const rows = [...resp.sectors].reverse(); // echarts y 类目自下而上 → 累计流入最强的放顶部
  const yLabels = rows.map((s) => s.sector_name);
  const data: [number, number, number][] = [];
  let maxAbs = 0;
  rows.forEach((s, y) => {
    s.values.forEach((v, x) => {
      if (v === null) return;
      maxAbs = Math.max(maxAbs, Math.abs(v));
      data.push([x, y, v]);
    });
  });
  return {
    grid: { left: 104, right: 8, top: 8, bottom: 28 },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: "category",
      data: yLabels,
      axisLabel: { color: UI.textFaint, width: 96, overflow: "truncate", fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    visualMap: {
      show: false,
      min: -(maxAbs || 1),
      max: maxAbs || 1,
      dimension: 2,
      inRange: { color: [UI.down, UI.bgPanel, UI.up] },
    },
    tooltip: {
      formatter: (p) => {
        const v = (p as unknown as { data: [number, number, number] }).data;
        return `${yLabels[v[1]]} · ${dates[v[0]]}<br/>净流入: ${fmtAmountCn(v[2], true)}`;
      },
    },
    series: [
      {
        type: "heatmap",
        data,
        itemStyle: { borderColor: UI.bgPanel, borderWidth: 1 },
      },
    ],
  };
}

/** 潜伏象限图 (决策地板): x=窗口涨跌, y=相对净流入, size=|累计净流|, color=behavior。
 *  左上=潜伏 (钱进价平)。只画满窗 known 指标; thin/unknown 不落 (0,0) (诚实门)。 */
export function latentQuadrantOption(rows: MoneyflowBoardRow[]): EChartsOption {
  // Only plot full-window known metrics. Thin/unknown never land at (0,0).
  const points = rows
    .filter(
      (r) =>
        r.horizon.status === "known" &&
        r.horizon.relative_ratio_pct != null &&
        r.horizon.window_return_pct != null,
    )
    .map((r) => {
      const cum = r.horizon.cum_net;
      const absCum = cum == null || Number.isNaN(cum) ? 0 : Math.abs(cum);
      const size = Math.max(8, Math.min(28, 8 + Math.sqrt(absCum / 1e8) * 10));
      const beh = r.behavior.behavior;
      return {
        value: [r.horizon.window_return_pct as number, r.horizon.relative_ratio_pct as number, size],
        name: r.sector_name ?? r.sector_code,
        sector_code: r.sector_code,
        behavior: beh,
        behavior_zh: r.behavior.behavior_zh,
        conclusion: r.conclusion,
        itemStyle: { color: BEHAVIOR_DOT[beh] ?? BEHAVIOR_DOT.unknown, opacity: beh === "unknown" ? 0.45 : 0.85 },
      };
    });

  return {
    animationDuration: 280,
    grid: { left: 56, right: 24, top: 36, bottom: 48 },
    xAxis: {
      name: "窗口涨跌 %",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: UI.textDim, fontSize: 11 },
      type: "value",
      axisLabel: { color: UI.textFaint, fontSize: 11, formatter: (v: number) => `${v}%` },
      axisLine: { lineStyle: { color: UI.border } },
      splitLine: { lineStyle: { color: UI.borderSoft } },
    },
    yAxis: {
      name: "相对净流入 %",
      nameTextStyle: { color: UI.textDim, fontSize: 11 },
      type: "value",
      axisLabel: { color: UI.textFaint, fontSize: 11, formatter: (v: number) => `${v}%` },
      axisLine: { lineStyle: { color: UI.border } },
      splitLine: { lineStyle: { color: UI.borderSoft } },
    },
    tooltip: {
      trigger: "item",
      formatter: (p: unknown) => {
        const d = (p as { data?: (typeof points)[number] }).data;
        if (!d) return "";
        const [x, y] = d.value;
        return [
          `<b>${d.name}</b>`,
          `${d.behavior_zh}`,
          `涨跌 ${x.toFixed(2)}% · 相对流入 ${y.toFixed(2)}%`,
          d.conclusion ? d.conclusion : "未形成结论",
        ].join("<br/>");
      },
    },
    series: [
      {
        type: "scatter",
        symbolSize: (val: number[]) => val[2],
        data: points,
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: UI.border, type: "dashed", width: 1 },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
          label: { show: false },
        },
        markArea: {
          silent: true,
          data: [
            [
              {
                name: "潜伏",
                xAxis: "min",
                yAxis: 0,
                itemStyle: { color: "rgba(91, 122, 157, 0.07)" },
                label: {
                  show: true,
                  position: "insideTopLeft",
                  color: "#5b7a9d",
                  fontSize: 12,
                  fontWeight: 600,
                },
              },
              { xAxis: 0, yAxis: "max" },
            ],
            [
              {
                name: "抢筹",
                xAxis: 0,
                yAxis: 0,
                itemStyle: { color: "rgba(212, 52, 44, 0.05)" },
                label: {
                  show: true,
                  position: "insideTopRight",
                  color: UI.up,
                  fontSize: 12,
                  fontWeight: 600,
                },
              },
              { xAxis: "max", yAxis: "max" },
            ],
          ],
        },
      },
    ],
  };
}
