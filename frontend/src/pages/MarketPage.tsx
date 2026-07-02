/** C4 市场感知页 — 5 卡片 widget 独立取数 (契约=analysis/market_pulse_design_20260702.md §3)。
 *  红线: 感知层只描述现状 (资金在哪/流向哪/温度如何), 零买卖暗示文案。 */
import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";
import {
  fetchHeatmap,
  fetchQuiet,
  fetchRotation,
  fetchSentiment,
  fetchWarnings,
} from "../api/pulse";
import type { HeatmapResp, QuietRow, RotationSector, SentimentPoint } from "../api/pulse";
import { Card, FetchGate } from "../components/Card";
import { EChart } from "../components/EChart";
import { fmtDate, fmtInt, fmtNum } from "../format";
import { useFetch } from "../hooks/useFetch";

/** rs_* 已是百分点 → 直接带符号显示, 不再 ×100。 */
function fmtPts(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(1)}`;
}

/** pct_change 源值即百分数 (2.5 = +2.5%)。 */
function fmtPctRaw(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${x > 0 ? "+" : ""}${x.toFixed(2)}%`;
}

const fmtMD = (d: string) => `${d.slice(4, 6)}-${d.slice(6, 8)}`;

// ── 卡1: 资金热力图 (dc 链, 板块×近N日 net_amount) ─────────────────────────

function heatmapOption(resp: HeatmapResp): EChartsOption {
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
    grid: { left: 104, right: 16, top: 8, bottom: 64 },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: "#9da7b3", fontSize: 10 },
      axisLine: { lineStyle: { color: "#30363d" } },
    },
    yAxis: {
      type: "category",
      data: yLabels,
      axisLabel: { color: "#9da7b3", width: 96, overflow: "truncate", fontSize: 11 },
      axisLine: { lineStyle: { color: "#30363d" } },
    },
    visualMap: {
      min: -(maxAbs || 1),
      max: maxAbs || 1,
      dimension: 2,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemWidth: 12,
      itemHeight: 90,
      textStyle: { color: "#6e7681", fontSize: 10 },
      // A股配色: 红=净流入, 绿=净流出, 深底=接近 0
      inRange: { color: ["#2ea065", "#161b22", "#e5534b"] },
    },
    tooltip: {
      formatter: (p) => {
        const v = (p as unknown as { data: [number, number, number] }).data;
        return `${yLabels[v[1]]} · ${dates[v[0]]}<br/>净流入: ${fmtNum(v[2], 0)}`;
      },
    },
    series: [
      {
        type: "heatmap",
        data,
        itemStyle: { borderColor: "#0d1117", borderWidth: 1 },
      },
    ],
  };
}

const HEATMAP_DAYS = [10, 20, 60] as const;

function HeatmapCard() {
  const [days, setDays] = useState<number>(20);
  const state = useFetch(() => fetchHeatmap({ chain: "dc_concept", days, top: 40 }), [days]);
  const option = useMemo(
    () => (state.data && state.data.sectors.length ? heatmapOption(state.data) : null),
    [state.data],
  );
  return (
    <Card
      title={`资金热力 (东财板块 × 近${days}日净流入, 按窗口累计净额前 40)`}
      extra={
        <div className="tab-group">
          {HEATMAP_DAYS.map((d) => (
            <button
              key={d}
              className={`btn tab${days === d ? " active" : ""}`}
              onClick={() => setDays(d)}
            >
              {d}日
            </button>
          ))}
        </div>
      }
    >
      <FetchGate state={state} empty={(d) => d.sectors.length === 0} emptyHint="暂无板块资金流数据">
        {(d) =>
          option ? <EChart option={option} height={Math.max(240, d.sectors.length * 16 + 130)} /> : null
        }
      </FetchGate>
    </Card>
  );
}

// ── 卡2: RS 轮动排名 (sw 链, 排名迁移箭头) ─────────────────────────────────

function MigrationCell(props: { s: RotationSector }) {
  const { prev_rs_rank_4w: prev, rs_rank_4w: cur } = props.s;
  if (prev === null || cur === null) return <span>—</span>;
  const diff = prev - cur; // >0 = 排名上移 (变强)
  if (diff === 0) return <span className="mono">→</span>;
  return diff > 0 ? (
    <span className="pos">↑ {diff}</span>
  ) : (
    <span className="neg">↓ {-diff}</span>
  );
}

function RotationCard() {
  const state = useFetch(() => fetchRotation({ lag: 5 }), []);
  return (
    <Card
      title="RS 轮动排名 (申万 L1 vs HS300, 4周/12周相对强度)"
      extra={
        state.data?.latest_date ? (
          <span className="inline-ctl">
            最新 {fmtDate(state.data.latest_date)} · 对比 {fmtDate(state.data.prev_date)} (5 交易日前)
          </span>
        ) : undefined
      }
    >
      <FetchGate state={state} empty={(d) => d.sectors.length === 0} emptyHint="暂无 RS 数据">
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>RS 排名(4周)</th>
                  <th>板块</th>
                  <th>RS 4周 (pp)</th>
                  <th>RS 12周 (pp)</th>
                  <th>上期排名</th>
                  <th>排名迁移</th>
                </tr>
              </thead>
              <tbody>
                {d.sectors.map((s) => (
                  <tr key={s.sector_code}>
                    <td className="mono">{s.rs_rank_4w ?? "—"}</td>
                    <td>{s.sector_name}</td>
                    <td className={s.rs_4w !== null && s.rs_4w > 0 ? "pos" : s.rs_4w !== null && s.rs_4w < 0 ? "neg" : ""}>
                      {fmtPts(s.rs_4w)}
                    </td>
                    <td className={s.rs_12w !== null && s.rs_12w > 0 ? "pos" : s.rs_12w !== null && s.rs_12w < 0 ? "neg" : ""}>
                      {fmtPts(s.rs_12w)}
                    </td>
                    <td className="mono">{s.prev_rs_rank_4w ?? "—"}</td>
                    <td>
                      <MigrationCell s={s} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 卡3: 悄悄流入 / 悄悄流出榜 (dc 链, 价稳+连续净流向) ────────────────────

function QuietTable(props: { rows: QuietRow[]; daysKey: "quiet_inflow_days" | "quiet_outflow_days" }) {
  if (props.rows.length === 0) return <div className="state-hint">当前无满足条件的板块</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>板块</th>
            <th>连续天数</th>
            <th>当日净额</th>
            <th>当日涨跌</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr key={`${r.chain}|${r.sector_code}`}>
              <td>{r.sector_name}</td>
              <td className="mono">{fmtInt(r[props.daysKey])}</td>
              <td className={r.net_amount !== null && r.net_amount > 0 ? "pos" : r.net_amount !== null && r.net_amount < 0 ? "neg" : ""}>
                {fmtNum(r.net_amount, 0)}
              </td>
              <td>{fmtPctRaw(r.pct_change)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QuietCard() {
  const state = useFetch(() => fetchQuiet({ limit: 20 }), []);
  return (
    <Card title="悄悄流入 / 悄悄流出 (价格波动 <1% 且资金连续净流向, 东财板块)">
      <FetchGate
        state={state}
        empty={(d) => d.inflow.length === 0 && d.outflow.length === 0}
        emptyHint="当前无价稳+连续净流向的板块"
      >
        {(d) => (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div>
              <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#9da7b3" }}>
                悄悄流入 (连续净流入天数降序)
              </h3>
              <QuietTable rows={d.inflow} daysKey="quiet_inflow_days" />
            </div>
            <div>
              <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#9da7b3" }}>
                悄悄流出 (连续净流出天数降序)
              </h3>
              <QuietTable rows={d.outflow} daysKey="quiet_outflow_days" />
            </div>
          </div>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 卡4: 情绪温度 (全市场, 双轴: 涨跌停家数 + 炸板率) ──────────────────────

function sentimentOption(days: SentimentPoint[]): EChartsOption {
  const dates = days.map((d) => fmtDate(d.trade_date));
  return {
    grid: { left: 52, right: 52, top: 32, bottom: 32 },
    legend: {
      data: ["涨停家数", "跌停家数", "炸板率%"],
      textStyle: { color: "#9da7b3" },
      top: 0,
    },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: "#9da7b3" },
      axisLine: { lineStyle: { color: "#30363d" } },
    },
    yAxis: [
      {
        type: "value",
        name: "家数",
        axisLabel: { color: "#9da7b3" },
        splitLine: { lineStyle: { color: "#21262d" } },
        nameTextStyle: { color: "#6e7681" },
      },
      {
        type: "value",
        name: "炸板率%",
        min: 0,
        max: 100,
        axisLabel: { color: "#9da7b3" },
        splitLine: { show: false },
        nameTextStyle: { color: "#6e7681" },
      },
    ],
    series: [
      {
        name: "涨停家数",
        type: "line",
        data: days.map((d) => d.limit_up_total),
        showSymbol: false,
        lineStyle: { width: 2, color: "#e5534b" },
        itemStyle: { color: "#e5534b" },
      },
      {
        name: "跌停家数",
        type: "line",
        data: days.map((d) => d.limit_down_total),
        showSymbol: false,
        lineStyle: { width: 1.5, color: "#2ea065" },
        itemStyle: { color: "#2ea065" },
      },
      {
        name: "炸板率%",
        type: "line",
        yAxisIndex: 1,
        data: days.map((d) => (d.zha_ban_rate === null ? null : +(d.zha_ban_rate * 100).toFixed(1))),
        showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", color: "#b8862d" },
        itemStyle: { color: "#b8862d" },
      },
    ],
  };
}

const SENTIMENT_DAYS = [60, 120, 250] as const;

function SentimentCard() {
  const [days, setDays] = useState<number>(120);
  const state = useFetch(() => fetchSentiment({ days }), [days]);
  const option = useMemo(
    () => (state.data && state.data.days.length ? sentimentOption(state.data.days) : null),
    [state.data],
  );
  const last = state.data?.days.length ? state.data.days[state.data.days.length - 1] : null;
  return (
    <Card
      title="情绪温度 (全市场: 涨跌停家数 + 炸板率)"
      extra={
        <div className="tab-group">
          {SENTIMENT_DAYS.map((d) => (
            <button
              key={d}
              className={`btn tab${days === d ? " active" : ""}`}
              onClick={() => setDays(d)}
            >
              {d}日
            </button>
          ))}
        </div>
      }
    >
      <FetchGate state={state} empty={(d) => d.days.length === 0} emptyHint="暂无情绪时序数据">
        {() => (
          <>
            {last && (
              <div className="kpi-grid" style={{ marginBottom: 10 }}>
                <div className="kpi">
                  <label>最新日 ({fmtDate(last.trade_date)}) 涨停</label>
                  <b className={last.limit_up_total ? "pos" : ""}>{fmtInt(last.limit_up_total)}</b>
                </div>
                <div className="kpi">
                  <label>跌停</label>
                  <b className={last.limit_down_total ? "neg" : ""}>{fmtInt(last.limit_down_total)}</b>
                </div>
                <div className="kpi">
                  <label>炸板率</label>
                  <b>{last.zha_ban_rate === null ? "—" : `${(last.zha_ban_rate * 100).toFixed(1)}%`}</b>
                </div>
                <div className="kpi">
                  <label>涨跌比 (涨/跌家数)</label>
                  <b>{fmtNum(last.adv_dec_ratio, 2)}</b>
                </div>
                <div className="kpi">
                  <label>大盘净流入</label>
                  <b>{fmtNum(last.mkt_net_amount, 0)}</b>
                </div>
              </div>
            )}
            {option ? <EChart option={option} height={300} /> : null}
          </>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 卡5: 退潮预警 (描述性: 跌出 RS top-N + 连续悄悄流出) ───────────────────

function WarningsCard() {
  const state = useFetch(fetchWarnings, []);
  return (
    <Card title="退潮预警 (描述性观察, 非操作建议)">
      <FetchGate
        state={state}
        empty={(d) => d.rank_dropouts.length === 0 && d.quiet_outflows.length === 0}
        emptyHint="两项预警均无满足条件的板块"
      >
        {(d) => (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <div>
              <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#9da7b3" }}>
                跌出 RS 前 {d.thresholds.rank_top} (申万 L1, 前一交易日 → 最新日)
              </h3>
              {d.rank_dropouts.length === 0 ? (
                <div className="state-hint">无板块跌出</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>板块</th>
                        <th>排名变化</th>
                        <th>RS 4周 (pp)</th>
                        <th>对比日</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.rank_dropouts.map((r) => (
                        <tr key={r.sector_code}>
                          <td>{r.sector_name}</td>
                          <td className="mono">
                            {r.prev_rank} → <span className="neg">{r.latest_rank}</span>
                          </td>
                          <td>{fmtPts(r.rs_4w)}</td>
                          <td className="mono">
                            {fmtMD(r.prev_date)}→{fmtMD(r.latest_date)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div>
              <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#9da7b3" }}>
                连续悄悄流出 ≥ {d.thresholds.quiet_outflow_days} 天 (东财板块)
              </h3>
              {d.quiet_outflows.length === 0 ? (
                <div className="state-hint">无满足条件的板块</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>板块</th>
                        <th>连续流出天数</th>
                        <th>当日净额</th>
                        <th>当日涨跌</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.quiet_outflows.map((r) => (
                        <tr key={r.sector_code}>
                          <td>{r.sector_name}</td>
                          <td className="mono">{fmtInt(r.quiet_outflow_days)}</td>
                          <td className="neg">{fmtNum(r.net_amount, 0)}</td>
                          <td>{fmtPctRaw(r.pct_change)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 页面 ───────────────────────────────────────────────────────────────────

export function MarketPage() {
  return (
    <div className="page">
      <h1>市场感知</h1>
      <p style={{ color: "#6e7681", fontSize: 12, margin: "-8px 0 14px" }}>
        感知层只描述资金现状与市场温度 (钱在哪 / 流向哪 / 悄悄动向), 不构成任何操作建议。
      </p>
      <HeatmapCard />
      <RotationCard />
      <QuietCard />
      <SentimentCard />
      <WarningsCard />
    </div>
  );
}
