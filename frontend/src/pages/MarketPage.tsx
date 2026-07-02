/** C4 市场感知页 — widget 独立取数 (契约=analysis/market_pulse_design_20260702.md §3)。
 *  红线: 感知层只描述现状 (资金在哪/流向哪/温度如何), 零买卖暗示文案。
 *  布局: 信息漏斗 顶区脉搏 KPI → 钱在哪 (热力/轮动) → 情绪周期 (天梯/温度) → 异动观察。
 *  配色: 浅色纸感, echarts 颜色走 theme.ts UI 常量 (与 styles.css :root 同步)。 */
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";
import {
  fetchDcRotation,
  fetchHeatmap,
  fetchQuiet,
  fetchRotation,
  fetchSentiment,
  fetchStrongest,
  fetchWarnings,
} from "../api/pulse";
import type {
  DcContentType,
  DcRotationSector,
  HeatmapResp,
  QuietRow,
  RotationSector,
  SentimentPoint,
} from "../api/pulse";
import { Card, FetchGate } from "../components/Card";
import { EChart } from "../components/EChart";
import { fmtDate, fmtInt, fmtNum } from "../format";
import { useFetch } from "../hooks/useFetch";
import { UI, UI_ALPHA } from "../theme";

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
      title={`资金热力 (东财${contentType} × 近${days}日净流入, 窗口累计前 40)`}
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
        {(d) =>
          option ? <EChart option={option} height={Math.max(240, d.sectors.length * 16 + 60)} /> : null
        }
      </FetchGate>
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

function SwRotationTable(props: { onMeta: (m: string | null) => void }) {
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
                  <tr key={s.sector_code}>
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

function DcRotationTable(props: { onMeta: (m: string | null) => void }) {
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
                  <tr key={s.sector_code}>
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
  const title =
    tab === "sw"
      ? "板块轮动 — 申万 L1 RS 排名 (vs HS300, 4周/12周相对强度)"
      : "板块轮动 — 东财板块资金流排名 (净流入截面排名 + 双龙头 + 流入宽度)";
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
      {tab === "sw" ? <SwRotationTable onMeta={setMeta} /> : <DcRotationTable onMeta={setMeta} />}
    </Card>
  );
}

// ── 卡3: 悄悄流入 / 悄悄流出榜 (dc 链, 价稳+连续净流向) ────────────────────

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

function QuietTable(props: {
  rows: QuietRow[];
  daysKey: "quiet_inflow_days" | "quiet_outflow_days";
}) {
  if (props.rows.length === 0) return <div className="state-hint">当前无满足条件的板块</div>;
  const dir = props.daysKey === "quiet_inflow_days" ? "in" : "out";
  return (
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
          {props.rows.map((r) => (
            <tr key={`${r.chain}|${r.sector_code}`}>
              <td>{r.sector_name}</td>
              <td>
                <Streak n={r[props.daysKey]} dir={dir} />
              </td>
              <td className={`mono ${signClass(r.net_amount)}`}>{fmtAmountCn(r.net_amount, true)}</td>
              <td className="mono">{fmtPctRaw(r.pct_change)}</td>
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
    <Card title="悄悄流入 / 流出 (价格波动 <1% 且资金连续净流向)">
      <FetchGate
        state={state}
        empty={(d) => d.inflow.length === 0 && d.outflow.length === 0}
        emptyHint="当前无价稳+连续净流向的板块"
      >
        {(d) => (
          <>
            <h3 className="sub-head">悄悄流入 (连续净流入天数降序)</h3>
            <QuietTable rows={d.inflow} daysKey="quiet_inflow_days" />
            <h3 className="sub-head" style={{ marginTop: 14 }}>
              悄悄流出 (连续净流出天数降序)
            </h3>
            <QuietTable rows={d.outflow} daysKey="quiet_outflow_days" />
          </>
        )}
      </FetchGate>
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

// ── 卡4b: 情绪温度时序 (涨停面积 + 跌停线 + 炸板率细线右轴) ────────────────

function sentimentOption(days: SentimentPoint[]): EChartsOption {
  const dates = days.map((d) => fmtDate(d.trade_date));
  return {
    grid: { left: 44, right: 44, top: 30, bottom: 28 },
    legend: {
      data: ["涨停家数", "跌停家数", "炸板率%"],
      textStyle: { color: UI.textDim, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
      top: 0,
    },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "家数",
        axisLabel: { color: UI.textFaint, fontSize: 11 },
        splitLine: { lineStyle: { color: UI.borderSoft } },
        nameTextStyle: { color: UI.textFaint, fontSize: 11 },
      },
      {
        type: "value",
        name: "炸板率%",
        min: 0,
        max: 100,
        axisLabel: { color: UI.textFaint, fontSize: 11 },
        splitLine: { show: false },
        nameTextStyle: { color: UI.textFaint, fontSize: 11 },
      },
    ],
    series: [
      {
        name: "涨停家数",
        type: "line",
        data: days.map((d) => d.limit_up_total),
        showSymbol: false,
        lineStyle: { width: 1.5, color: UI.up },
        itemStyle: { color: UI.up },
        areaStyle: { color: UI_ALPHA.upArea }, // 浅红半透明面积 = 情绪主视觉
      },
      {
        name: "跌停家数",
        type: "line",
        data: days.map((d) => d.limit_down_total),
        showSymbol: false,
        lineStyle: { width: 1, color: UI.down },
        itemStyle: { color: UI.down },
      },
      {
        name: "炸板率%",
        type: "line",
        yAxisIndex: 1,
        data: days.map((d) => (d.zha_ban_rate === null ? null : +(d.zha_ban_rate * 100).toFixed(1))),
        showSymbol: false,
        lineStyle: { width: 1, color: UI.warn },
        itemStyle: { color: UI.warn },
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
      title="情绪温度时序 (涨跌停家数 + 炸板率)"
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
            {option ? <EChart option={option} height={300} /> : null}
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

// ── 卡6: 退潮预警 (描述性: 跌出 RS top-N + 连续悄悄流出) ───────────────────

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
        感知层只描述资金现状与市场温度 (钱在哪 / 流向哪 / 悄悄动向), 不构成任何操作建议。
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
        <QuietCard />
        <StrongestCard />
        <WarningsCard />
      </div>
    </div>
  );
}
