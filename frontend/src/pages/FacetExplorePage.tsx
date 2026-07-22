/**
 * Facet explore — L2 universe for a computed facet.
 * Consumes Cap A board / Cap B screener / Cap D intersection; no ad-hoc math.
 */
import { Link, useSearchParams } from "react-router-dom";
import {
  fetchIntersectionStrongest,
  fetchMoneyflowBoard,
  type AssistBehavior,
  type AssistHorizon,
} from "../api/decision";
import {
  fetchScreenerFormStage,
  type ScreenerAxisPos,
  type ScreenerAxisPurity,
  type ScreenerAxisTrend,
  type ScreenerAxisVol,
} from "../api/screener";
import { Card, FetchGate } from "../components/Card";
import { fmtDate } from "../format";
import {
  axisLabel,
  behaviorLabel,
  marketDeepLink,
  type FacetKind,
} from "../facet/registry";
import { useFetch } from "../hooks/useFetch";

function parseKind(raw: string | null): FacetKind | null {
  const allowed: FacetKind[] = [
    "behavior",
    "form_name",
    "axis_pos",
    "axis_trend",
    "axis_purity",
    "axis_vol",
    "breakout",
    "intersection",
  ];
  return raw && (allowed as string[]).includes(raw) ? (raw as FacetKind) : null;
}

function BehaviorUniverse(props: { value: string; horizon: number }) {
  const state = useFetch(
    () => fetchMoneyflowBoard({ chain: "dc_industry", horizon: props.horizon as AssistHorizon, limit: 80 }),
    [props.horizon],
  );
  return (
    <FetchGate state={state} empty={(d) => d.rows.length === 0} emptyHint="当前窗无板块行">
      {(d) => {
        const rows = d.rows.filter(
          (r) => r.behavior.behavior === (props.value as AssistBehavior),
        );
        return (
          <>
            <p className="page-lead">
              板块行为「{behaviorLabel(props.value)}」· {props.horizon}日窗 · as-of{" "}
              {d.as_of ? fmtDate(d.as_of) : "—"}。点板块结论可回市场象限；个股跳转走档案。
            </p>
            <p className="page-desc">
              <Link
                to={marketDeepLink({ tab: "assist", behavior: props.value })}
              >
                在市场 · 资金决策辅助中打开同筛选
              </Link>
            </p>
            {rows.length === 0 ? (
              <div className="state-hint">该行为下暂无已知窗板块（诚实空态）</div>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>板块</th>
                      <th>行为</th>
                      <th>相对流入</th>
                      <th>窗口涨跌</th>
                      <th>结论</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.sector_code}>
                        <td>
                          {r.sector_name ?? r.sector_code}
                          <div className="mono muted">{r.sector_code}</div>
                        </td>
                        <td>{r.behavior.behavior_zh}</td>
                        <td className="mono">
                          {r.horizon.relative_ratio_pct != null
                            ? `${r.horizon.relative_ratio_pct.toFixed(2)}%`
                            : "未知"}
                        </td>
                        <td className="mono">
                          {r.horizon.window_return_pct != null
                            ? `${r.horizon.window_return_pct.toFixed(2)}%`
                            : "未知"}
                        </td>
                        <td className="assist-conclusion">{r.conclusion ?? "未形成结论"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted dossier-note">{d.disclaimer}</p>
          </>
        );
      }}
    </FetchGate>
  );
}

function ScreenerUniverse(props: {
  kind: FacetKind;
  value: string;
}) {
  const formNames = props.kind === "form_name" ? [props.value] : [];
  const axisPos =
    props.kind === "axis_pos" ? (props.value as ScreenerAxisPos) : undefined;
  const axisTrend =
    props.kind === "axis_trend" ? (props.value as ScreenerAxisTrend) : undefined;
  const axisPurity =
    props.kind === "axis_purity" ? (props.value as ScreenerAxisPurity) : undefined;
  const axisVol =
    props.kind === "axis_vol" ? (props.value as ScreenerAxisVol) : undefined;
  const breakout = props.kind === "breakout" ? true : undefined;

  const title =
    props.kind === "form_name"
      ? `形态 · ${props.value}`
      : props.kind === "breakout"
        ? "突破日"
        : `${axisLabel(props.kind, props.value)}`;

  const state = useFetch(
    () =>
      fetchScreenerFormStage({
        formNames,
        axisPos,
        axisTrend,
        axisPurity,
        axisVol,
        isBreakoutEvent: breakout,
        limit: 50,
      }),
    [props.kind, props.value],
  );

  const marketLink = marketDeepLink({
    tab: "screener",
    formName: props.kind === "form_name" ? props.value : undefined,
    axisPos: axisPos,
    axisTrend: axisTrend,
    axisPurity: axisPurity,
    axisVol: axisVol,
    breakout: Boolean(breakout),
  });

  return (
    <FetchGate
      state={state}
      empty={(d) => d.rows.length === 0}
      emptyHint="当前 facet 无匹配个股（诚实空态）"
    >
      {(d) => (
        <>
          <p className="page-lead">
            {title} · as-of {d.as_of ? fmtDate(d.as_of) : "—"} · {d.count} 条
            {d.truncated ? "（已截断）" : ""}
          </p>
          <p className="page-desc">
            <Link to={marketLink}>在市场 · 形态/阶段选股中打开同筛选</Link>
          </p>
          {d.status === "stale" ? (
            <div className="state-hint">数据过期（{d.reason}）— 不展示假新鲜结果</div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>形态</th>
                    <th>轴</th>
                    <th>why</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.stock_code}>
                      <td>
                        <Link to={`/stock/${r.stock_code}`}>
                          <b>{r.stock_name ?? "—"}</b>
                          <span className="mono muted"> {r.stock_code}</span>
                        </Link>
                      </td>
                      <td>
                        {r.form_name ?? "—"}
                        {r.is_breakout_event ? (
                          <span className="badge-breakout">突破</span>
                        ) : null}
                      </td>
                      <td className="muted">
                        {[r.axis_pos_zh, r.axis_trend_zh, r.axis_purity_zh, r.axis_vol_zh]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </td>
                      <td className="assist-conclusion">{r.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted dossier-note">{d.disclaimer}</p>
        </>
      )}
    </FetchGate>
  );
}

function IntersectionUniverse() {
  const state = useFetch(() => fetchIntersectionStrongest({ horizon: 20, limit: 40 }), []);
  return (
    <FetchGate
      state={state}
      empty={(d) => d.rows.length === 0}
      emptyHint="当前窗无交集个股（诚实空态）"
    >
      {(d) => (
        <>
          <p className="page-lead">
            三链交集最强 · 东财行业∩概念∩申万 ·{" "}
            <Link to={marketDeepLink({ tab: "intersection" })}>市场页打开</Link>
          </p>
          {d.status === "stale" ? (
            <div className="state-hint">过期（{d.reason}）</div>
          ) : (
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>why</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.stock_code}>
                      <td>
                        <Link to={`/stock/${r.stock_code}`}>
                          <b>{r.stock_name ?? "—"}</b>
                          <span className="mono muted"> {r.stock_code}</span>
                        </Link>
                      </td>
                      <td className="assist-conclusion">{r.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted dossier-note">{d.disclaimer}</p>
        </>
      )}
    </FetchGate>
  );
}

export function FacetExplorePage() {
  const [params] = useSearchParams();
  const kind = parseKind(params.get("kind"));
  const value = (params.get("value") || "").trim();
  const from = (params.get("from") || "").trim();
  const horizon = Number(params.get("horizon") || "20") || 20;

  if (!kind || (kind !== "intersection" && kind !== "breakout" && !value)) {
    return (
      <div className="page">
        <h1>探索</h1>
        <p className="page-lead">缺少 facet 参数。请从档案或市场页的可点击标签进入。</p>
        <Link className="back-link" to="/market">
          ← 市场
        </Link>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          {from ? (
            <Link className="back-link" to={`/stock/${from}`}>
              ← 返回 {from}
            </Link>
          ) : (
            <Link className="back-link" to="/market">
              ← 市场
            </Link>
          )}
          <h1>探索 · facet 宇宙</h1>
        </div>
      </div>
      <Card title="L2 同 facet 名单（预计算积木，非浏览器现算）">
        {kind === "behavior" && <BehaviorUniverse value={value} horizon={horizon} />}
        {(kind === "form_name" ||
          kind === "axis_pos" ||
          kind === "axis_trend" ||
          kind === "axis_purity" ||
          kind === "axis_vol" ||
          kind === "breakout") && <ScreenerUniverse kind={kind} value={value || "1"} />}
        {kind === "intersection" && <IntersectionUniverse />}
      </Card>
    </div>
  );
}
