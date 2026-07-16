import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { fetchProfiles, fetchSignals } from "../api/inst";
import { addPosition } from "../api/paper";
import type { InstSignal, ProfileOrderBy } from "../api/types";
import { Card, FetchGate } from "../components/Card";
import { fmtDate, fmtInt, fmtPct, pnlClass } from "../format";
import { emitTopic, useFetch } from "../hooks/useFetch";

const SORTABLE: { key: ProfileOrderBy; label: string }[] = [
  { key: "median_alpha", label: "超额中位" },
  { key: "avg_alpha", label: "平均超额" },
  { key: "win_rate_alpha", label: "胜率(超额)" },
  { key: "n_closed", label: "已了结数" },
];

function RankingCard() {
  const navigate = useNavigate();
  const [orderBy, setOrderBy] = useState<ProfileOrderBy>("median_alpha");
  // min_episodes=1 时后端也返回 low_sample 行 (前端灰显), 默认 10 = 后端排名护栏 MIN_EPISODES
  const [minEpisodes, setMinEpisodes] = useState(10);
  const state = useFetch(
    () => fetchProfiles({ orderBy, minEpisodes, limit: 100 }),
    [orderBy, minEpisodes],
  );

  return (
    <Card
      title="机构排名"
      extra={
        <label className="inline-ctl">
          最少 episode 数
          <input
            type="number"
            min={1}
            max={1000}
            value={minEpisodes}
            onChange={(e) => setMinEpisodes(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
      }
    >
      <FetchGate state={state} empty={(d) => d.length === 0} emptyHint="无满足条件的机构 (调低最少 episode 数试试)">
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>机构</th>
                  <th>类型</th>
                  {SORTABLE.map((c) => (
                    <th
                      key={c.key}
                      className={`sortable${orderBy === c.key ? " sorted" : ""}`}
                      onClick={() => setOrderBy(c.key)}
                      title="点击按此列排序 (服务端排序)"
                    >
                      {c.label}
                      {orderBy === c.key ? " ↓" : ""}
                    </th>
                  ))}
                  <th>收益中位</th>
                  <th>平均持有(天)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.holder}
                    className={`clickable${r.low_sample ? " low-sample" : ""}`}
                    title={r.low_sample ? "样本不足 (episode < 10), 不进正式排名" : r.holder}
                    onClick={() => navigate(`/institutions/${encodeURIComponent(r.holder)}`)}
                  >
                    <td className="holder-name">{r.holder}</td>
                    <td>{r.holder_type ?? "—"}</td>
                    <td className={pnlClass(r.median_alpha)}>{fmtPct(r.median_alpha)}</td>
                    <td className={pnlClass(r.avg_alpha)}>{fmtPct(r.avg_alpha)}</td>
                    <td>{fmtPct(r.win_rate_alpha)}</td>
                    <td>{fmtInt(r.n_closed)}</td>
                    <td className={pnlClass(r.median_ret)}>{fmtPct(r.median_ret)}</td>
                    <td>{fmtInt(r.avg_hold_days)}</td>
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

/** 披露事件可显式记入 legacy 观察账本；这不是跟随信号或成交动作。 */
function FollowCell(props: { signal: InstSignal }) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async () => {
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      setMsg("请输入正数金额");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const d = await addPosition({
        stock_code: props.signal.stock,
        amount: amt,
        strategy_tag: "inst_disclosure_observation",
        note: `观察披露机构 ${props.signal.holder}`,
      });
      setMsg(`已记入观察 ${d.shares} 股 @ ${d.entry_price}（qfq 近似值）`);
      setOpen(false);
      emitTopic("paper");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="follow-cell">
      {!open ? (
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          记入观察
        </button>
      ) : (
        <span className="follow-form">
          <input
            type="number"
            placeholder="金额 (元)"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            disabled={busy}
          />
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? "…" : "确认"}
          </button>
          <button className="btn" onClick={() => setOpen(false)} disabled={busy}>
            取消
          </button>
        </span>
      )}
      {msg && <div className="follow-msg">{msg}</div>}
    </div>
  );
}

function SignalsCard() {
  const navigate = useNavigate();
  // ?stock=600001: 市场感知页下钻叶子行跳转入口 (v3) — 客户端过滤信号流到该股
  const [params, setParams] = useSearchParams();
  const stockFilter = params.get("stock");
  const state = useFetch(() => fetchSignals({ days: 90, limit: 50 }), []);

  return (
    <Card
      title="最新披露事件研究流 (近 90 天)"
      extra={
        stockFilter ? (
          <span className="inline-ctl">
            筛选标的 <b className="mono">{stockFilter}</b>{" "}
            <button
              className="btn"
              onClick={() => {
                params.delete("stock");
                setParams(params, { replace: true });
              }}
            >
              清除
            </button>
          </span>
        ) : undefined
      }
    >
      <FetchGate
        state={state}
        empty={(d) => d.filter((s) => !stockFilter || s.stock.includes(stockFilter)).length === 0}
        emptyHint={
          stockFilter
            ? `近 90 天无 ${stockFilter} 的披露事件 (清除筛选看全部)`
            : "近 90 天无满足条件的披露事件"
        }
      >
        {(all) => {
          const signals = all.filter((s) => !stockFilter || s.stock.includes(stockFilter));
          return (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>机构</th>
                  <th>标的</th>
                  <th>建仓期</th>
                  <th>披露日</th>
                  <th>行业(PIT)</th>
                  <th>机构战绩 (超额中位 / 胜率 / n)</th>
                  <th>观察账本</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={`${s.holder}|${s.stock}|${s.open_date}`}>
                    <td
                      className="holder-name clickable"
                      title={s.holder}
                      onClick={() => navigate(`/institutions/${encodeURIComponent(s.holder)}`)}
                    >
                      {s.holder}
                    </td>
                    <td className="mono">{s.stock}</td>
                    <td>{fmtDate(s.open_date)}</td>
                    <td>{fmtDate(s.open_notice)}</td>
                    <td>{s.sw_l1_at_open ?? "—"}</td>
                    <td>
                      <span className={pnlClass(s.holder_median_alpha)}>
                        {fmtPct(s.holder_median_alpha)}
                      </span>
                      {" / "}
                      {fmtPct(s.holder_win_rate)}
                      {" / "}
                      {fmtInt(s.holder_n_closed)}
                    </td>
                    <td>
                      <FollowCell signal={s} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          );
        }}
      </FetchGate>
    </Card>
  );
}

export function InstitutionsPage() {
  return (
    <div className="page">
      <h1>机构档案</h1>
      <RankingCard />
      <SignalsCard />
    </div>
  );
}
