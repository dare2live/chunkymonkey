import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";
import { addPosition, closePosition, fetchNav, fetchPortfolio, markToMarket } from "../api/paper";
import type { PaperNavPoint } from "../api/types";
import { Card, FetchGate } from "../components/Card";
import { EChart } from "../components/EChart";
import { fmtDate, fmtNum, fmtPct, pnlClass } from "../format";
import { emitTopic, useFetch } from "../hooks/useFetch";
import { UI } from "../theme";

const STRATEGY_TAGS = ["manual_observation", "inst_disclosure_observation"];

/** 组合概览卡: KPI 走 /portfolio, 现金/市值走 /nav 最新快照 (kpi 不含这两项)。 */
function OverviewCard() {
  const state = useFetch(
    () => Promise.all([fetchPortfolio(), fetchNav()] as const),
    [],
    ["paper"],
  );
  return (
    <Card title="组合概览">
      <FetchGate state={state}>
        {([{ kpi }, nav]) => {
          const last = nav.length ? nav[nav.length - 1] : null;
          return (
            <>
              <div className="kpi-grid">
                <div className="kpi">
                  <label>初始资金</label>
                  <b>{fmtNum(kpi.init_cash, 0)}</b>
                </div>
                <div className="kpi">
                  <label>现金</label>
                  <b>{last ? fmtNum(last.cash, 0) : "—"}</b>
                </div>
                <div className="kpi">
                  <label>持仓市值</label>
                  <b>{last ? fmtNum(last.position_value, 0) : "—"}</b>
                </div>
                <div className="kpi">
                  <label>总资产 (nav)</label>
                  <b>{kpi.nav !== undefined ? fmtNum(kpi.nav, 0) : "—"}</b>
                </div>
                <div className="kpi">
                  <label>累计收益</label>
                  <b className={pnlClass(kpi.ret_cum)}>{fmtPct(kpi.ret_cum, 2)}</b>
                </div>
                <div className="kpi">
                  <label>HS300 同期</label>
                  <b className={pnlClass(kpi.bench_ret_cum)}>{fmtPct(kpi.bench_ret_cum, 2)}</b>
                </div>
                <div className="kpi">
                  <label>超额</label>
                  <b className={pnlClass(kpi.excess_cum)}>{fmtPct(kpi.excess_cum, 2)}</b>
                </div>
                <div className="kpi">
                  <label>已了结胜率</label>
                  <b>
                    {fmtPct(kpi.win_rate)} ({kpi.n_closed})
                  </b>
                </div>
              </div>
              {!last && (
                <div className="state-hint">尚无估值快照 — 点右上「手动估值」生成 qfq-close 观察值</div>
              )}
            </>
          );
        }}
      </FetchGate>
    </Card>
  );
}

function navOption(nav: PaperNavPoint[]): EChartsOption {
  const dates = nav.map((p) => p.nav_date);
  const base = nav[0].nav;
  const benchBase = nav.find((p) => p.bench_close !== null)?.bench_close ?? null;
  const navSeries = nav.map((p) => +(p.nav / base).toFixed(4));
  const benchSeries = nav.map((p) =>
    p.bench_close !== null && benchBase ? +(p.bench_close / benchBase).toFixed(4) : null,
  );
  return {
    grid: { left: 52, right: 12, top: 30, bottom: 28 },
    legend: { data: ["组合", "HS300"], textStyle: { color: UI.textDim, fontSize: 11 }, top: 0 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: dates,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: UI.textFaint, fontSize: 11 },
      splitLine: { lineStyle: { color: UI.borderSoft } },
    },
    series: [
      {
        name: "组合",
        type: "line",
        data: navSeries,
        showSymbol: false,
        lineStyle: { width: 2, color: UI.accent },
        itemStyle: { color: UI.accent },
      },
      {
        name: "HS300",
        type: "line",
        data: benchSeries,
        showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", color: UI.textDim },
        itemStyle: { color: UI.textDim },
      },
    ],
  };
}

function NavChartCard() {
  const state = useFetch(fetchNav, [], ["paper"]);
  const option = useMemo(() => (state.data && state.data.length ? navOption(state.data) : null), [state.data]);
  return (
    <Card title="观察估值曲线 (归一, 组合 vs HS300)">
      <FetchGate state={state} empty={(d) => d.length === 0} emptyHint="暂无估值数据 — 记入观察后点「手动估值」开始记录">
        {() => (option ? <EChart option={option} height={300} /> : null)}
      </FetchGate>
    </Card>
  );
}

function PositionsCard() {
  const state = useFetch(fetchPortfolio, [], ["paper"]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const doClose = async (positionId: string, stock: string) => {
    if (!window.confirm(`确认结束观察 ${stock}（按最新完成交易日 qfq close 近似记账）?`)) return;
    setBusyId(positionId);
    setMsg(null);
    try {
      const d = await closePosition(positionId);
      setMsg(`已结束观察 ${d.stock_code}: 近似盈亏 ${fmtNum(d.pnl, 0)} 元 (${d.ret_pct}%)`);
      emitTopic("paper");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card title="观察条目">
      {msg && <div className="banner-info">{msg}</div>}
      <FetchGate state={state} empty={(d) => d.positions.length === 0} emptyHint="暂无观察条目 — 用下方表单或机构披露研究流记入">
        {({ positions }) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>策略</th>
                  <th>股数</th>
                  <th>记入日</th>
                  <th>记入价(qfq)</th>
                  <th>状态</th>
                  <th>结束日</th>
                  <th>结束价(qfq)</th>
                  <th title="近似盈亏 = 股数×(结束 qfq−记入 qfq), 不含真实成交约束">
                    毛盈亏(不含费)
                  </th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const pnl =
                    p.status === "closed" && p.exit_price !== null
                      ? p.shares * (p.exit_price - p.entry_price)
                      : null;
                  return (
                    <tr key={p.position_id} className={p.status === "closed" ? "low-sample" : ""}>
                      <td className="mono">{p.stock_code}</td>
                      <td>
                        <span className="tag">{p.strategy_tag}</span>
                      </td>
                      <td>{fmtNum(p.shares, 0)}</td>
                      <td>{fmtDate(p.entry_date)}</td>
                      <td>{fmtNum(p.entry_price)}</td>
                      <td>{p.status === "open" ? <span className="tag tag-hold">持有</span> : "已平"}</td>
                      <td>{fmtDate(p.exit_date)}</td>
                      <td>{p.exit_price !== null ? fmtNum(p.exit_price) : "—"}</td>
                      <td className={pnlClass(pnl)}>{pnl !== null ? fmtNum(pnl, 0) : "—"}</td>
                      <td>
                        {p.status === "open" && (
                          <button
                            className="btn btn-danger"
                            disabled={busyId === p.position_id}
                            onClick={() => doClose(p.position_id, p.stock_code)}
                          >
                            {busyId === p.position_id ? "…" : "结束观察"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </FetchGate>
    </Card>
  );
}

function AddPositionCard() {
  const [stockCode, setStockCode] = useState("");
  const [amount, setAmount] = useState("");
  const [tag, setTag] = useState(STRATEGY_TAGS[0]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setMsg(null);
    setErr(null);
    if (!/^\d{6}$/.test(stockCode)) {
      setErr("股票代码须为 6 位数字");
      return;
    }
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      setErr("金额须为正数");
      return;
    }
    setBusy(true);
    try {
      const d = await addPosition({ stock_code: stockCode, amount: amt, strategy_tag: tag, note });
      setMsg(`已记入观察 ${d.stock_code}: ${d.shares} 股 @ ${d.entry_price}（估算费 ${d.fee}）`);
      setStockCode("");
      setAmount("");
      setNote("");
      emitTopic("paper");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="手动记入观察">
      <div className="form-row">
        <label>
          股票代码
          <input
            value={stockCode}
            placeholder="600519"
            maxLength={6}
            onChange={(e) => setStockCode(e.target.value.trim())}
            disabled={busy}
          />
        </label>
        <label>
          金额 (元)
          <input
            type="number"
            value={amount}
            placeholder="按最新收盘价换算整手"
            onChange={(e) => setAmount(e.target.value)}
            disabled={busy}
          />
        </label>
        <label>
          策略标签
          <select value={tag} onChange={(e) => setTag(e.target.value)} disabled={busy}>
            {STRATEGY_TAGS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          备注
          <input value={note} onChange={(e) => setNote(e.target.value)} disabled={busy} />
        </label>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? "提交中…" : "记入观察"}
        </button>
      </div>
      {err && <div className="state-error-inline">{err}</div>}
      {msg && <div className="banner-info">{msg}</div>}
    </Card>
  );
}

function MarkButton() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const doMark = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const d = await markToMarket();
      setMsg(`已更新 ${d.nav_date}: nav ${fmtNum(d.nav, 0)}`);
      emitTopic("paper");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mark-ctl">
      {msg && <span className="mark-msg">{msg}</span>}
      <button className="btn btn-primary" onClick={doMark} disabled={busy}>
        {busy ? "估值中…" : "手动估值"}
      </button>
    </div>
  );
}

export function PaperPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>手工观察账本</h1>
          <p className="state-hint">NONCONFORMING：qfq-close 近似记账，不是订单/成交模拟或策略发布证据。</p>
        </div>
        <MarkButton />
      </div>
      <OverviewCard />
      <NavChartCard />
      <PositionsCard />
      <AddPositionCard />
    </div>
  );
}
