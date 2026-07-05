/** C4 市场感知页 — widget 独立取数 (契约=analysis/market_pulse_design_20260702.md §3 + v3 设计)。
 *  红线: 感知层只描述现状 (资金在哪/流向哪/什么形态/温度如何), 零买卖暗示文案。
 *  措辞规范 (v3.4 + 2026-07-03 用户纠偏): flow_regime 中文显示名用克制金融术语
 *  (脉冲/横盘累积/上行累积/下行累积), 禁口语化/戏剧化字样。
 *  布局: 信息漏斗 顶区脉搏 KPI → 钱在哪 (热力/轮动) → 情绪周期 (天梯/温度) → 异动观察。
 *  v3 交互: 热力图/轮动表/资金流向榜 全模块行点击 → DrillPanel inline 逐层下钻
 *  (sw L1→L2→L3→成分股 / dc 板块→成分股), 叶子行点击跳 /institutions 检索该股。
 *  配色: 浅色纸感, echarts 颜色走 theme.ts UI 常量 (与 styles.css :root 同步)。 */
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  fetchDcRotation,
  fetchDrill,
  fetchFlowBoard,
  fetchFlowStripe,
  fetchHeatmap,
  fetchRotation,
  fetchSentiment,
  fetchStrongest,
  fetchWarnings,
} from "../api/pulse";
import type {
  DcContentType,
  DcRotationSector,
  DrillSectorRow,
  DrillStockRow,
  FlowBoardRow,
  FlowRegime,
  HeatmapResp,
  PulseChain,
  RotationSector,
  SentimentPoint,
} from "../api/pulse";
import { Card, FetchGate } from "../components/Card";
import { EChart } from "../components/EChart";
import { fmtDate, fmtInt, fmtNum } from "../format";
import { useFetch } from "../hooks/useFetch";
import { UI } from "../theme";

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

/** 金额自适应中文单位 (源值=元 → 万/亿), 缺失 "—" (不用 0 糊弄)。 */
function fmtAmountCn(x: number | null | undefined, signed = false): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const sign = signed && x > 0 ? "+" : "";
  const a = Math.abs(x);
  if (a >= 1e8) return `${sign}${(x / 1e8).toFixed(2)}亿`;
  if (a >= 1e4) return `${sign}${(x / 1e4).toFixed(0)}万`;
  return `${sign}${x.toFixed(0)}`;
}

/** 连板天梯 JSON ({"1":50,"2":10}) → 升序 [板数, 家数]; null (源缺日) / 解析失败 → []。 */
function parseLadder(j: string | null): [string, number][] {
  if (!j) return [];
  try {
    const o = JSON.parse(j) as Record<string, number>;
    return Object.entries(o).sort((a, b) => Number(a[0]) - Number(b[0]));
  } catch {
    return [];
  }
}

const fmtMD = (d: string) => `${d.slice(4, 6)}-${d.slice(6, 8)}`;

/** 涨跌语义 class (A股红涨绿跌); 0/缺失无色。 */
function signClass(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x) || x === 0) return "";
  return x > 0 ? "pos" : "neg";
}

// ── v3: 资金流形态 (flow_regime) 展示层 ────────────────────────────────────

/** flow_regime 中文显示名 (克制金融术语, 2026-07-03 用户定稿; key 与引擎入库值一致)。 */
const REGIME_META: Record<FlowRegime, { label: string; dir: "in" | "out" | "" }> = {
  surge_in: { label: "脉冲流入", dir: "in" },
  accum_in_silent: { label: "横盘累积流入", dir: "in" },
  accum_in_driving: { label: "上行累积流入", dir: "in" },
  surge_out: { label: "脉冲流出", dir: "out" },
  accum_out_silent: { label: "横盘累积流出", dir: "out" },
  accum_out_driving: { label: "下行累积流出", dir: "out" },
  neutral: { label: "无显著形态", dir: "" },
};

function RegimeTag({ regime }: { regime: FlowRegime | null | undefined }) {
  if (!regime || !(regime in REGIME_META)) return <span>—</span>;
  const m = REGIME_META[regime];
  return <span className={`regime-tag${m.dir ? ` ${m.dir}` : ""}`}>{m.label}</span>;
}

/** mini flow-stripe (v3.3): 逐日净流入色带 (红入绿出, 白≈零, 灰=缺日), 高 14px,
 *  |值| 按 p95 封顶归一防单日极值压扁色阶; hover 原生 title 显日值。 */
function MiniFlowStripe({ dates, values }: { dates: string[]; values: (number | null)[] }) {
  const cap = p95(values.filter((v): v is number => v !== null).map(Math.abs));
  return (
    <span className="flow-stripe" title="逐日净流入 (红入绿出)">
      {values.map((v, i) => {
        let bg = "var(--bg-panel-2)";
        if (v !== null) {
          const t = Math.min(1, Math.abs(v) / cap);
          const rgb = v >= 0 ? STRIPE_RGB.up : STRIPE_RGB.down;
          bg = `rgba(${rgb}, ${(t * 0.92).toFixed(3)})`;
        }
        return (
          <i
            key={i}
            style={{ background: bg }}
            title={`${fmtDate(dates[i] ?? "")} ${v === null ? "无数据" : fmtAmountCn(v, true)}`}
          />
        );
      })}
    </span>
  );
}

// ── v3.5: 资金流向曲线 (逐日净流条纹之外的连续趋势视图) ──────────────────────
// 设计: 单板块用"当日净流(柱) + 累计净流(线)"双轴组合 (细节视图, 缺日置零不连断);
// 多板块对比用"累计净流(线)"单轴叠加 (趋势对比, 不做柱防止多组柱子互相遮挡)。
// 累计从窗口首日起算 (非全历史累计), 仅表达"本窗口内资金净流入/流出的持续性", 非绝对仓位。

/** 缺日(null)按0处理后前缀和 — 窗口内累计净流, 用于表达持续性趋势 (非绝对量)。 */
function cumulativeSeries(values: (number | null)[]): number[] {
  let acc = 0;
  return values.map((v) => {
    acc += v ?? 0;
    return acc;
  });
}

const CURVE_PALETTE = [
  "#3b66d4", "#d4342c", "#0f8a4e", "#a06a00", "#7d5ba6", "#1f8a8c", "#c2703d", "#5c6bc0",
];

/** 多板块累计净流对比曲线 (topN 叠加, 单轴; 悬浮框汇总当日各线数值)。 */
function multiSectorCurveOption(dates: string[], series: { name: string; values: (number | null)[] }[]): EChartsOption {
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
function sectorDrillCurveOption(dates: string[], values: (number | null)[]): EChartsOption {
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

// ── v3: 统一下钻面板 (热力图/轮动表/资金流向榜 三处共用, 防三份实现漂移) ────

export interface DrillTarget {
  chain: PulseChain;
  code: string;
  name: string | null;
}

function DrillSectorTable(props: {
  rows: DrillSectorRow[];
  chain: PulseChain;
  onEnter: (code: string) => void;
}) {
  if (props.rows.length === 0) return <div className="state-hint">该层无子节点数据</div>;
  const sw = props.chain === "sw_industry";
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>板块</th>
            <th>层级</th>
            <th>当日涨跌</th>
            <th>当日净流</th>
            <th>资金形态</th>
            <th>近20日占市值</th>
            <th>{sw ? "RS 4周 (pp) / 排名" : "资金流排名"}</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr key={r.sector_code} className="clickable" onClick={() => props.onEnter(r.sector_code)}>
              <td>{r.sector_name ?? r.sector_code}</td>
              <td className="mono">{r.level ?? "—"}</td>
              <td className={`mono ${signClass(r.pct_change)}`}>{fmtPctRaw(r.pct_change)}</td>
              <td className={`mono ${signClass(r.net_amount)}`}>{fmtAmountCn(r.net_amount, true)}</td>
              <td>
                <RegimeTag regime={r.flow_regime} />
              </td>
              <td className="mono">
                {typeof r.cum_ratio_20d === "number" ? `${r.cum_ratio_20d.toFixed(2)}%` : "—"}
              </td>
              <td className="mono">
                {sw
                  ? `${fmtPts(r.rs_4w)} / ${r.rs_rank_4w ?? "—"}`
                  : (r.rank_flow ?? "—")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DrillStockTable(props: { rows: DrillStockRow[] }) {
  const navigate = useNavigate();
  if (props.rows.length === 0) return <div className="state-hint">无成分股数据</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>20日形态 (B2)</th>
            <th>资金形态</th>
            <th>近20日净流</th>
            <th>当日净流</th>
            <th>连板</th>
            <th>当日涨跌</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr
              key={r.ts_code}
              className="clickable"
              title="点击到机构档案页检索该股"
              onClick={() => navigate(`/institutions?stock=${encodeURIComponent(r.stock_code)}`)}
            >
              <td className="mono">{r.stock_code}</td>
              <td>{r.name ?? "—"}</td>
              <td>
                {r.form_name ?? "—"}
                {r.is_breakout_event ? <span className="badge-breakout">突破</span> : null}
              </td>
              <td>
                <RegimeTag regime={r.flow_regime} />
              </td>
              <td className={`mono ${signClass(r.cum_net)}`}>{fmtAmountCn(r.cum_net, true)}</td>
              <td className={`mono ${signClass(r.net_amount)}`}>{fmtAmountCn(r.net_amount, true)}</td>
              <td className="mono">{r.limit_times != null ? `${r.limit_times}板` : "—"}</td>
              <td className={`mono ${signClass(r.pct_change)}`}>{fmtPctRaw(r.pct_change)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CRUMB_LEVEL_CN: Record<string, string> = { L1: "一级", L2: "二级", L3: "三级", sector: "板块" };

/** 统一下钻面板: target 为入口板块 (卡片行点击注入); 内部管理当前节点, 面包屑回退。 */
const DRILL_CURVE_DAYS = [60, 120, 250] as const;

/** 当前下钻节点的资金流曲线 (柱=当日净流/线=窗口累计净流); 独立取数, 节点切换即重取。 */
function DrillCurve(props: { chain: PulseChain; code: string; name: string | null }) {
  const [days, setDays] = useState<number>(120);
  const state = useFetch(
    () => fetchFlowStripe({ code: props.code, chain: props.chain, days }),
    [props.chain, props.code, days],
  );
  const option = useMemo(
    () => (state.data && state.data.dates.length ? sectorDrillCurveOption(state.data.dates, state.data.values) : null),
    [state.data],
  );
  return (
    <div className="drill-curve">
      <div className="drill-curve-head">
        <span className="section-label">
          {(state.data?.sector_name ?? props.name ?? props.code)} · 资金流向曲线
        </span>
        <div className="tab-group">
          {DRILL_CURVE_DAYS.map((n) => (
            <button
              key={n}
              className={`btn tab${days === n ? " active" : ""}`}
              onClick={() => setDays(n)}
            >
              {n}日
            </button>
          ))}
        </div>
      </div>
      <FetchGate state={state} empty={(x) => x.dates.length === 0} emptyHint="该节点暂无资金流历史">
        {() => (option ? <EChart option={option} height={180} /> : null)}
      </FetchGate>
    </div>
  );
}

function DrillPanel(props: { target: DrillTarget; onClose: () => void }) {
  const { target } = props;
  const [code, setCode] = useState<string>(target.code);
  useEffect(() => setCode(target.code), [target.chain, target.code]);
  const state = useFetch(
    () => fetchDrill({ chain: target.chain, code }),
    [target.chain, code],
  );
  const d = state.data;
  return (
    <div className="drill-panel">
      <div className="drill-head">
        <span className="drill-crumbs">
          {(d?.breadcrumb?.length ? d.breadcrumb : [{ code: target.code, name: target.name, level: "" }]).map(
            (c, i, arr) => (
              <span key={c.code}>
                <button
                  className={`btn crumb${i === arr.length - 1 ? " active" : ""}`}
                  onClick={() => setCode(c.code)}
                  title={c.level ? `${CRUMB_LEVEL_CN[c.level] ?? c.level}` : undefined}
                >
                  {c.name ?? c.code}
                </button>
                {i < arr.length - 1 && <span className="crumb-sep">›</span>}
              </span>
            ),
          )}
          {d?.rows_level === "stock" && <span className="crumb-sep">› 成分股</span>}
        </span>
        <span className="drill-meta">
          {d?.date && <span className="inline-ctl">数据日 {fmtDate(d.date)}</span>}
          <button className="btn" onClick={props.onClose}>
            收起
          </button>
        </span>
      </div>
      <DrillCurve
        chain={target.chain}
        code={code}
        name={(d?.breadcrumb?.length ? d.breadcrumb[d.breadcrumb.length - 1].name : null) ?? target.name}
      />
      <FetchGate state={state} empty={(x) => x.rows.length === 0} emptyHint="该节点无下层数据">
        {(resp) =>
          resp.rows_level === "stock" ? (
            <DrillStockTable rows={resp.rows} />
          ) : (
            <DrillSectorTable rows={resp.rows} chain={resp.chain} onEnter={setCode} />
          )
        }
      </FetchGate>
    </div>
  );
}

// ── 顶区: 市场脉搏 KPI 带 (大号 mono 数字 + 环比小箭头) ────────────────────

function PulseKpi(props: {
  label: string;
  value: string;
  valueCls?: string;
  /** 环比差值 (对前一交易日); null = 无法计算显示占位 */
  delta: number | null;
  fmtDelta: (d: number) => string;
}) {
  const { delta } = props;
  return (
    <div className="pulse-kpi">
      <label title={props.label}>{props.label}</label>
      <div className={`pulse-num mono${props.valueCls ? ` ${props.valueCls}` : ""}`}>
        {props.value}
      </div>
      {delta === null ? (
        <div className="pulse-delta mono">—</div>
      ) : delta === 0 ? (
        <div className="pulse-delta mono">持平</div>
      ) : (
        <div className={`pulse-delta mono ${delta > 0 ? "pos" : "neg"}`}>
          {delta > 0 ? "▲" : "▼"} {props.fmtDelta(Math.abs(delta))}
        </div>
      )}
    </div>
  );
}

function PulseBand() {
  const state = useFetch(() => fetchSentiment({ days: 5 }), []);
  return (
    <FetchGate state={state} empty={(d) => d.days.length === 0} emptyHint="暂无市场脉搏数据">
      {(d) => {
        const last = d.days[d.days.length - 1];
        const prev = d.days.length > 1 ? d.days[d.days.length - 2] : null;
        const diff = (cur: number | null, pre: number | null | undefined) =>
          cur != null && pre != null ? cur - pre : null;
        return (
          <>
            <div className="section-label">市场脉搏 · {fmtDate(last.trade_date)} (环比前一交易日)</div>
            <div className="pulse-band">
              <PulseKpi
                label="涨跌比 (涨/跌家数)"
                value={fmtNum(last.adv_dec_ratio, 2)}
                valueCls={
                  last.adv_dec_ratio == null ? "" : last.adv_dec_ratio >= 1 ? "pos" : "neg"
                }
                delta={diff(last.adv_dec_ratio, prev?.adv_dec_ratio)}
                fmtDelta={(x) => x.toFixed(2)}
              />
              <PulseKpi
                label="涨停"
                value={fmtInt(last.limit_up_total)}
                valueCls={last.limit_up_total ? "pos" : ""}
                delta={diff(last.limit_up_total, prev?.limit_up_total)}
                fmtDelta={(x) => String(Math.round(x))}
              />
              <PulseKpi
                label="跌停"
                value={fmtInt(last.limit_down_total)}
                valueCls={last.limit_down_total ? "neg" : ""}
                delta={diff(last.limit_down_total, prev?.limit_down_total)}
                fmtDelta={(x) => String(Math.round(x))}
              />
              <PulseKpi
                label="炸板率"
                value={last.zha_ban_rate === null ? "—" : `${(last.zha_ban_rate * 100).toFixed(1)}%`}
                delta={diff(last.zha_ban_rate, prev?.zha_ban_rate)}
                fmtDelta={(x) => `${(x * 100).toFixed(1)}pp`}
              />
              <PulseKpi
                label="两融余额"
                value={fmtAmountCn(last.rzrqye)}
                delta={last.rzrqye_chg}
                fmtDelta={(x) => fmtAmountCn(x)}
              />
              <PulseKpi
                label="沪深300 PE(TTM)"
                value={fmtNum(last.mkt_pe, 2)}
                delta={diff(last.mkt_pe, prev?.mkt_pe)}
                fmtDelta={(x) => x.toFixed(2)}
              />
            </div>
          </>
        );
      }}
    </FetchGate>
  );
}

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
    // 分向色阶 绿→白→红, 白=零点: 零流入自然隐没, 强流向自然跳出; 图例藏起
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

const HEATMAP_DAYS = [10, 20, 60] as const;
const HEATMAP_CONTENT_TYPES: DcContentType[] = ["行业", "概念"];

function HeatmapCard() {
  const [days, setDays] = useState<number>(20);
  const [contentType, setContentType] = useState<DcContentType>("行业");
  const [drill, setDrill] = useState<DrillTarget | null>(null);
  const state = useFetch(
    () => fetchHeatmap({ chain: "dc_concept", content_type: contentType, days, top: 40 }),
    [days, contentType],
  );
  const option = useMemo(
    () => (state.data && state.data.sectors.length ? heatmapOption(state.data) : null),
    [state.data],
  );
  return (
    <Card
      title={`资金热力 (东财${contentType} × 近${days}日净流入, 窗口累计前 40; 点击格子下钻成分)`}
      extra={
        <div className="tab-group">
          {HEATMAP_CONTENT_TYPES.map((ct) => (
            <button
              key={ct}
              className={`btn tab${contentType === ct ? " active" : ""}`}
              onClick={() => setContentType(ct)}
            >
              {ct}
            </button>
          ))}
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
        {(d) => {
          // y 轴倒序渲染 (heatmapOption 同一 reverse), 点击格子 y 索引 → 板块
          const rows = [...d.sectors].reverse();
          return option ? (
            <EChart
              option={option}
              height={Math.max(240, d.sectors.length * 16 + 60)}
              onClick={(p) => {
                const cell = p.data as [number, number, number] | undefined;
                const sec = cell ? rows[cell[1]] : undefined;
                if (sec) {
                  setDrill({ chain: "dc_concept", code: sec.sector_code, name: sec.sector_name });
                }
              }}
            />
          ) : null;
        }}
      </FetchGate>
      {drill && <DrillPanel target={drill} onClose={() => setDrill(null)} />}
    </Card>
  );
}

// ── 卡2: 板块轮动 (sw=RS 排名迁移 / dc=资金流排名迁移 + 双龙头, v2 分链 tab) ─

/** 表格内联微条形: 数字右侧 40px 细条按 |值|/max 比例, 红正绿负 — 数据条对齐扫读。 */
function BarCell(props: { value: number | null; maxAbs: number; text: string }) {
  const { value, maxAbs, text } = props;
  return (
    <span className="cellbar">
      <span className={`num mono ${signClass(value)}`}>{text}</span>
      <span className="track">
        {value != null && value !== 0 && maxAbs > 0 && (
          <i
            className={`fill ${value > 0 ? "pos-f" : "neg-f"}`}
            style={{ width: `${Math.min(100, (Math.abs(value) / maxAbs) * 100)}%` }}
          />
        )}
      </span>
    </span>
  );
}

function MigrationArrow(props: { prev: number | null; cur: number | null }) {
  const { prev, cur } = props;
  // == null 双杀 null/undefined (旧 API 响应缺字段时给 undefined, 不许 NaN 上屏)
  if (prev == null || cur == null) return <span>—</span>;
  const diff = prev - cur; // >0 = 排名上移 (变强)
  if (diff === 0) return <span className="mono">→</span>;
  return diff > 0 ? (
    <span className="pos">↑ {diff}</span>
  ) : (
    <span className="neg">↓ {-diff}</span>
  );
}

function MigrationCell(props: { s: RotationSector }) {
  return <MigrationArrow prev={props.s.prev_rs_rank_4w} cur={props.s.rs_rank_4w} />;
}

function SwRotationTable(props: {
  onMeta: (m: string | null) => void;
  onDrill: (t: DrillTarget) => void;
}) {
  const state = useFetch(() => fetchRotation({ lag: 5 }), []);
  const meta = state.data?.latest_date
    ? `最新 ${fmtDate(state.data.latest_date)} · 对比 ${fmtDate(state.data.prev_date)} (5 交易日前)`
    : null;
  // meta 提升到卡头 (副作用走 effect, 不在 render 期间 set 父状态)
  const { onMeta } = props;
  useEffect(() => onMeta(meta), [meta, onMeta]);
  return (
    <FetchGate state={state} empty={(d) => d.sectors.length === 0} emptyHint="暂无 RS 数据">
      {(d) => {
        const maxRs4 = d.sectors.reduce((m, s) => Math.max(m, Math.abs(s.rs_4w ?? 0)), 0);
        return (
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
                  <tr
                    key={s.sector_code}
                    className="clickable"
                    title="点击逐层下钻 (L1 → L2 → L3 → 成分股)"
                    onClick={() =>
                      props.onDrill({ chain: "sw_industry", code: s.sector_code, name: s.sector_name })
                    }
                  >
                    <td className="mono">{s.rs_rank_4w ?? "—"}</td>
                    <td>{s.sector_name}</td>
                    <td>
                      <BarCell value={s.rs_4w} maxAbs={maxRs4} text={fmtPts(s.rs_4w)} />
                    </td>
                    <td className={signClass(s.rs_12w)}>{fmtPts(s.rs_12w)}</td>
                    <td className="mono">{s.prev_rs_rank_4w ?? "—"}</td>
                    <td>
                      <MigrationCell s={s} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </FetchGate>
  );
}

/** 龙头单元: 涨幅龙头 (dc_index) + 资金龙头 (moneyflow_ind_dc 净流入最大股)。 */
function LeaderCell(props: { s: DcRotationSector }) {
  const { leading, leading_pct, flow_leader_stock } = props.s;
  if (!leading && !flow_leader_stock) return <span>—</span>;
  return (
    <span>
      {leading ? (
        <span>
          {leading}
          <span className={leading_pct !== null && leading_pct < 0 ? "neg" : "pos"}>
            {" "}
            {fmtPctRaw(leading_pct)}
          </span>
        </span>
      ) : (
        "—"
      )}
      <span className="sep"> / </span>
      {flow_leader_stock ?? "—"}
    </span>
  );
}

function DcRotationTable(props: {
  onMeta: (m: string | null) => void;
  onDrill: (t: DrillTarget) => void;
}) {
  const state = useFetch(() => fetchDcRotation({ lag: 5, top: 20 }), []);
  const meta = state.data?.latest_date
    ? `最新 ${fmtDate(state.data.latest_date)} · 对比 ${fmtDate(state.data.prev_date)} (5 交易日前) · 资金流排名前 20`
    : null;
  const { onMeta } = props;
  useEffect(() => onMeta(meta), [meta, onMeta]);
  return (
    <FetchGate state={state} empty={(d) => d.sectors.length === 0} emptyHint="暂无东财板块资金流数据">
      {(d) => {
        const maxNet = d.sectors.reduce((m, s) => Math.max(m, Math.abs(s.net_amount ?? 0)), 0);
        return (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>资金流排名</th>
                  <th>板块</th>
                  <th>类型</th>
                  <th>当日净额</th>
                  <th>当日涨跌</th>
                  <th>流入宽度</th>
                  <th>涨幅龙头 / 资金龙头</th>
                  <th>排名迁移</th>
                </tr>
              </thead>
              <tbody>
                {d.sectors.map((s) => (
                  <tr
                    key={s.sector_code}
                    className="clickable"
                    title="点击下钻成分股"
                    onClick={() =>
                      props.onDrill({ chain: "dc_concept", code: s.sector_code, name: s.sector_name })
                    }
                  >
                    <td className="mono">{s.rank_flow ?? "—"}</td>
                    <td>{s.sector_name}</td>
                    <td>{s.content_type ?? "—"}</td>
                    <td>
                      <BarCell
                        value={s.net_amount}
                        maxAbs={maxNet}
                        text={fmtAmountCn(s.net_amount, true)}
                      />
                    </td>
                    <td className={signClass(s.pct_change)}>{fmtPctRaw(s.pct_change)}</td>
                    <td className="mono">
                      {typeof s.inflow_breadth === "number"
                        ? `${(s.inflow_breadth * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td>
                      <LeaderCell s={s} />
                    </td>
                    <td>
                      <MigrationArrow prev={s.prev_rank_flow} cur={s.rank_flow} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </FetchGate>
  );
}

const ROTATION_TABS = [
  { key: "sw", label: "申万 RS" },
  { key: "dc", label: "东财资金" },
] as const;

function RotationCard() {
  const [tab, setTab] = useState<"sw" | "dc">("sw");
  const [meta, setMeta] = useState<string | null>(null);
  const [drill, setDrill] = useState<DrillTarget | null>(null);
  const title =
    tab === "sw"
      ? "板块轮动 — 申万 L1 RS 排名 (vs HS300, 4周/12周相对强度; 行点击逐层下钻)"
      : "板块轮动 — 东财板块资金流排名 (净流入截面排名 + 双龙头 + 流入宽度; 行点击下钻)";
  return (
    <Card
      title={title}
      extra={
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
          {meta && <span className="inline-ctl">{meta}</span>}
          <span className="tab-group">
            {ROTATION_TABS.map((t) => (
              <button
                key={t.key}
                className={`btn tab${tab === t.key ? " active" : ""}`}
                onClick={() => {
                  setMeta(null);
                  setDrill(null);
                  setTab(t.key);
                }}
              >
                {t.label}
              </button>
            ))}
          </span>
        </span>
      }
    >
      {tab === "sw" ? (
        <SwRotationTable onMeta={setMeta} onDrill={setDrill} />
      ) : (
        <DcRotationTable onMeta={setMeta} onDrill={setDrill} />
      )}
      {drill && <DrillPanel target={drill} onClose={() => setDrill(null)} />}
    </Card>
  );
}

// ── 卡3: 资金流向榜 (v3 — flow_regime 形态分组 + 量级 + mini 条纹; 替代 v1 quiet 榜) ──

/** 连续天数 → GitHub contribution 式方块串 (流入红/流出绿, 上限 8 后 +n)。 */
function Streak(props: { n: number | null; dir: "in" | "out" }) {
  if (props.n == null || Number.isNaN(props.n)) return <span>—</span>;
  const n = Math.round(props.n);
  if (n <= 0) return <span>—</span>;
  const shown = Math.min(n, 8);
  return (
    <span className="streak" title={`连续 ${n} 天`}>
      {Array.from({ length: shown }, (_, i) => (
        <i key={i} className={props.dir} />
      ))}
      {n > 8 && <span className="more mono">+{n - 8}</span>}
    </span>
  );
}

function FlowBoardTable(props: {
  rows: FlowBoardRow[];
  stripeDates: string[];
  onDrill: (t: DrillTarget) => void;
}) {
  if (props.rows.length === 0) return <div className="state-hint">当前无该组形态的板块</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>板块</th>
            <th>类型</th>
            <th>形态</th>
            <th>累计净额(近20日)</th>
            <th>占市值</th>
            <th>当日涨跌</th>
            <th>60日资金条纹</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr
              key={`${r.chain}|${r.sector_code}`}
              className="clickable"
              title="点击下钻成分"
              onClick={() =>
                props.onDrill({ chain: r.chain, code: r.sector_code, name: r.sector_name })
              }
            >
              <td>{r.sector_name}</td>
              <td>{r.content_type ?? "—"}</td>
              <td>
                <RegimeTag regime={r.flow_regime} />
              </td>
              <td className={`mono ${signClass(r.cum_net)}`}>{fmtAmountCn(r.cum_net, true)}</td>
              <td className="mono">
                {typeof r.cum_ratio_20d === "number" ? `${r.cum_ratio_20d.toFixed(2)}%` : "—"}
              </td>
              <td className={`mono ${signClass(r.pct_change)}`}>{fmtPctRaw(r.pct_change)}</td>
              <td>
                <MiniFlowStripe dates={props.stripeDates} values={r.stripe} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const FLOW_BOARD_TABS = [
  { key: "in", label: "流入形态" },
  { key: "out", label: "流出形态" },
] as const;

const MULTI_CURVE_TOPN = [3, 5, 8] as const;

/** 多板块累计净流对比曲线: 当前 tab (流入/流出) 前 topN 板块叠加, 复用 flow_board 已取的 stripe。 */
function MultiSectorCurve(props: { rows: FlowBoardRow[]; stripeDates: string[] }) {
  const [topN, setTopN] = useState<number>(5);
  const picked = props.rows.slice(0, topN);
  const option = useMemo(
    () =>
      picked.length
        ? multiSectorCurveOption(
            props.stripeDates,
            picked.map((r) => ({ name: r.sector_name, values: r.stripe })),
          )
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [picked.map((r) => r.sector_code).join(","), props.stripeDates],
  );
  if (!option) return <div className="state-hint">暂无可对比板块</div>;
  return (
    <div className="multi-curve">
      <div className="drill-curve-head">
        <span className="section-label">累计净流对比 (近{props.stripeDates.length}日, 趋势非绝对仓位)</span>
        <div className="tab-group">
          {MULTI_CURVE_TOPN.map((n) => (
            <button
              key={n}
              className={`btn tab${topN === n ? " active" : ""}`}
              onClick={() => setTopN(n)}
            >
              前{n}
            </button>
          ))}
        </div>
      </div>
      <EChart option={option} height={220} />
    </div>
  );
}

function FlowBoardCard() {
  const [tab, setTab] = useState<"in" | "out">("in");
  const [drill, setDrill] = useState<DrillTarget | null>(null);
  const state = useFetch(() => fetchFlowBoard({ chain: "dc_concept", limit: 20, stripe_days: 60 }), []);
  return (
    <Card
      title="资金流向榜 (东财板块链 · 形态=资金流分类学: 脉冲 / 横盘累积 / 上行·下行累积)"
      extra={
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>
          {state.data?.trade_date && (
            <span className="inline-ctl">{fmtDate(state.data.trade_date)}</span>
          )}
          <span className="tab-group">
            {FLOW_BOARD_TABS.map((t) => (
              <button
                key={t.key}
                className={`btn tab${tab === t.key ? " active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </span>
        </span>
      }
    >
      <FetchGate
        state={state}
        empty={(d) => d.inflow.length === 0 && d.outflow.length === 0}
        emptyHint="当前无显著资金流形态的板块"
      >
        {(d) => {
          const rows = tab === "in" ? d.inflow : d.outflow;
          return (
            <>
              <MultiSectorCurve rows={rows} stripeDates={d.stripe_dates} />
              <FlowBoardTable rows={rows} stripeDates={d.stripe_dates} onDrill={setDrill} />
            </>
          );
        }}
      </FetchGate>
      {drill && <DrillPanel target={drill} onClose={() => setDrill(null)} />}
    </Card>
  );
}

// ── 卡4a: 连板天梯 (横向漏斗条形, 纯 CSS; 晋级率=今日k板÷昨日k-1板 实测) ───

function LadderCard() {
  const state = useFetch(() => fetchSentiment({ days: 5 }), []);
  const last = state.data?.days.length ? state.data.days[state.data.days.length - 1] : null;
  return (
    <Card
      title="连板天梯 (limit_list_d 官方口径, 不含 ST)"
      extra={last ? <span className="inline-ctl">{fmtDate(last.trade_date)}</span> : undefined}
    >
      <FetchGate state={state} empty={(d) => d.days.length === 0} emptyHint="暂无连板数据">
        {(d) => {
          const cur = d.days[d.days.length - 1];
          const prevDay = d.days.length > 1 ? d.days[d.days.length - 2] : null;
          const dist = parseLadder(cur.limit_times_dist_json);
          const prevDist = prevDay
            ? new Map(parseLadder(prevDay.limit_times_dist_json).map(([k, n]) => [Number(k), n]))
            : null;
          const maxN = dist.reduce((m, [, n]) => Math.max(m, n), 0);
          return (
            <>
              {dist.length === 0 ? (
                <div className="state-hint">
                  {cur.limit_times_dist_json == null ? "源缺日 (未知)" : "当日无涨停"}
                </div>
              ) : (
                <div className="ladder">
                  {dist.map(([lt, n]) => {
                    const k = Number(lt);
                    const prevN = prevDist?.get(k - 1);
                    const rate = k >= 2 && prevN ? n / prevN : null;
                    return (
                      <div className="ladder-row" key={lt}>
                        <span className="ladder-label mono">{lt}板</span>
                        <span className="ladder-track">
                          <i
                            className="ladder-bar"
                            style={{ width: `${maxN ? Math.max((n / maxN) * 100, 1.5) : 0}%` }}
                          />
                          <b className="ladder-count mono">{n}</b>
                        </span>
                        <span
                          className="ladder-rate mono"
                          title={
                            rate !== null
                              ? `今日${k}板 ${n} ÷ 昨日${k - 1}板 ${prevN}`
                              : undefined
                          }
                        >
                          {rate !== null ? `晋级 ${(rate * 100).toFixed(0)}%` : ""}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="meta-strip">
                <span>
                  最高连板
                  <b>{typeof cur.max_limit_times === "number" ? `${cur.max_limit_times}板` : "—"}</b>
                </span>
                <span>
                  晋级率(≥2板/昨≥1板)
                  <b>
                    {typeof cur.promotion_rate === "number"
                      ? `${(cur.promotion_rate * 100).toFixed(1)}%`
                      : "—"}
                  </b>
                </span>
                <span>
                  秒板(09:30前)
                  <b>{fmtInt(cur.sec_board_n)}</b>
                </span>
                <span>
                  炸板总次数
                  <b>{fmtInt(cur.open_times_total)}</b>
                </span>
                <span>
                  封单均额
                  <b>{fmtAmountCn(cur.avg_fd_amount)}</b>
                </span>
              </div>
            </>
          );
        }}
      </FetchGate>
    </Card>
  );
}

// ── 卡4b: 情绪温度条纹带 (climate-stripes 式: 每交易日一根色条, 三条平行光谱;
//    无合成权重 — 三个变量各占一带, 不造"综合情绪分") ────────────────────────

/** p95 分位封顶 (防单日极值压扁全带的色阶动态范围)。 */
function p95(vals: number[]): number {
  if (!vals.length) return 1;
  const sorted = [...vals].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 1;
}

/** 白→语义色插值: v/cap 越大颜色越深; null=源缺日显灰纹。 */
function stripeColor(v: number | null, cap: number, rgb: string): string {
  if (v === null) return "var(--bg-panel-2)";
  const t = Math.min(1, v / cap);
  return `rgba(${rgb}, ${(t * 0.92).toFixed(3)})`;
}

const STRIPE_RGB = { up: "212, 52, 44", down: "15, 138, 78", warn: "160, 106, 0" } as const;

function StripeRow({
  label, values, cap, rgb, hoverIdx, onHover,
}: {
  label: string; values: (number | null)[]; cap: number; rgb: string;
  hoverIdx: number | null; onHover: (i: number | null) => void;
}) {
  return (
    <div className="stripe-row">
      <span className="stripe-label">{label}</span>
      <div className="stripe-band" onMouseLeave={() => onHover(null)}>
        {values.map((v, i) => (
          <span
            key={i}
            className={`stripe-cell${hoverIdx === i ? " hover" : ""}`}
            style={{ background: stripeColor(v, cap, rgb) }}
            onMouseEnter={() => onHover(i)}
          />
        ))}
      </div>
    </div>
  );
}

function SentimentStripes({ days }: { days: SentimentPoint[] }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const ups = days.map((d) => d.limit_up_total);
  const downs = days.map((d) => d.limit_down_total);
  const zhas = days.map((d) => (d.zha_ban_rate === null ? null : d.zha_ban_rate * 100));
  const upCap = useMemo(() => p95(ups.filter((v): v is number => v !== null)), [days]);
  const downCap = useMemo(() => p95(downs.filter((v): v is number => v !== null)), [days]);
  const cur = hoverIdx !== null ? days[hoverIdx] : days[days.length - 1];
  return (
    <div>
      <div className="stripe-readout mono">
        <span className="stripe-date">{fmtDate(cur.trade_date)}</span>
        <span>涨停 <b className="pos">{fmtInt(cur.limit_up_total)}</b></span>
        <span>跌停 <b className="neg">{fmtInt(cur.limit_down_total)}</b></span>
        <span>炸板率 <b>{cur.zha_ban_rate === null ? "—" : `${(cur.zha_ban_rate * 100).toFixed(1)}%`}</b></span>
        {hoverIdx === null && <span className="stripe-hint">悬停条纹查看历史</span>}
      </div>
      <StripeRow label="涨停" values={ups} cap={upCap} rgb={STRIPE_RGB.up} hoverIdx={hoverIdx} onHover={setHoverIdx} />
      <StripeRow label="跌停" values={downs} cap={downCap} rgb={STRIPE_RGB.down} hoverIdx={hoverIdx} onHover={setHoverIdx} />
      <StripeRow label="炸板率" values={zhas} cap={100} rgb={STRIPE_RGB.warn} hoverIdx={hoverIdx} onHover={setHoverIdx} />
      <div className="stripe-axis">
        <span>{fmtDate(days[0].trade_date)}</span>
        <span>{fmtDate(days[Math.floor(days.length / 2)].trade_date)}</span>
        <span>{fmtDate(days[days.length - 1].trade_date)}</span>
      </div>
    </div>
  );
}

const SENTIMENT_DAYS = [60, 120, 250] as const;

function SentimentCard() {
  const [days, setDays] = useState<number>(120);
  const state = useFetch(() => fetchSentiment({ days }), [days]);
  const last = state.data?.days.length ? state.data.days[state.data.days.length - 1] : null;
  return (
    <Card
      title="情绪温度 (limit_list_d 官方口径, 不含 ST; 每日一格: 涨停 / 跌停 / 炸板率 三条光谱带)"
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
              <div className="meta-strip">
                <span>
                  大盘净流入<b>{fmtAmountCn(last.mkt_net_amount, true)}</b>
                </span>
                <span>
                  两融日增
                  <b className={signClass(last.rzrqye_chg)}>{fmtAmountCn(last.rzrqye_chg, true)}</b>
                </span>
                <span>
                  换手率(自由流通)
                  <b>
                    {typeof last.mkt_turnover === "number" ? `${last.mkt_turnover.toFixed(2)}%` : "—"}
                  </b>
                </span>
                <span>
                  龙虎榜家数<b>{fmtInt(last.lhb_count)}</b>
                </span>
                <span>
                  龙虎榜席位净买
                  <b className={signClass(last.lhb_inst_net)}>{fmtAmountCn(last.lhb_inst_net, true)}</b>
                </span>
              </div>
            )}
            {state.data?.days.length ? <SentimentStripes days={state.data.days} /> : null}
          </>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 卡5: 最强板块 (limit_cpt_list, TI 码独立卡 — 禁与 dc/sw 链拼接) ────────

function StrongestCard() {
  const state = useFetch(fetchStrongest, []);
  return (
    <Card
      title="最强板块榜 (涨停板块热点排名, 同花顺口径)"
      extra={
        state.data?.trade_date ? (
          <span className="inline-ctl">榜单日 {fmtDate(state.data.trade_date)}</span>
        ) : undefined
      }
    >
      <FetchGate state={state} empty={(d) => d.sectors.length === 0} emptyHint="暂无最强板块榜数据">
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>热点排名</th>
                  <th>板块</th>
                  <th>连续上榜</th>
                  <th>连板高度</th>
                  <th>连板家数</th>
                  <th>涨停家数</th>
                  <th>板块涨跌</th>
                </tr>
              </thead>
              <tbody>
                {d.sectors.map((s) => (
                  <tr key={s.ts_code}>
                    <td className="mono">{s.rank ?? "—"}</td>
                    <td>{s.name}</td>
                    <td className="mono">{fmtInt(s.days)}</td>
                    <td>{s.up_stat ?? "—"}</td>
                    <td className="mono">{s.cons_nums ?? "—"}</td>
                    <td className="mono">{fmtInt(s.up_nums)}</td>
                    <td className={`mono ${signClass(s.pct_chg)}`}>{fmtPctRaw(s.pct_chg)}</td>
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

// ── 卡6: 退潮预警 (描述性: 跌出 RS top-N + 价稳连续净流出) ─────────────────

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
          <>
            <h3 className="sub-head">
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
                        <td className="mono">{fmtPts(r.rs_4w)}</td>
                        <td className="mono">
                          {fmtMD(r.prev_date)}→{fmtMD(r.latest_date)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <h3 className="sub-head" style={{ marginTop: 14 }}>
              价稳连续净流出 ≥ {d.thresholds.quiet_outflow_days} 天 (东财板块)
            </h3>
            {d.quiet_outflows.length === 0 ? (
              <div className="state-hint">无满足条件的板块</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>板块</th>
                      <th>持续</th>
                      <th>当日净额</th>
                      <th>当日涨跌</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.quiet_outflows.map((r) => (
                      <tr key={r.sector_code}>
                        <td>{r.sector_name}</td>
                        <td>
                          <Streak n={r.quiet_outflow_days} dir="out" />
                        </td>
                        <td className="mono neg">{fmtAmountCn(r.net_amount, true)}</td>
                        <td className="mono">{fmtPctRaw(r.pct_change)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </FetchGate>
    </Card>
  );
}

// ── 页面 (信息漏斗: 宏观 → 资金 → 情绪 → 异动) ─────────────────────────────

export function MarketPage() {
  return (
    <div className="page">
      <h1>市场感知</h1>
      <p className="page-desc">
        感知层只描述资金现状与市场温度 (钱在哪 / 流向哪 / 什么形态), 不构成任何操作建议。
      </p>
      <PulseBand />
      <div className="section-label">钱在哪 — 资金分布与轮动</div>
      <div className="grid-2">
        <HeatmapCard />
        <RotationCard />
      </div>
      <div className="section-label">情绪周期</div>
      <div className="grid-2">
        <LadderCard />
        <SentimentCard />
      </div>
      <div className="section-label">异动观察</div>
      <div className="grid-3">
        <FlowBoardCard />
        <StrongestCard />
        <WarningsCard />
      </div>
    </div>
  );
}
