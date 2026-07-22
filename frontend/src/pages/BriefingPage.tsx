/**
 * CX-3 daily briefing — narrative consumer of Cap A/B/D bricks.
 * Fail-closed: stale/UNTRUSTED inputs → no narrative (backend gate).
 */
import { Link } from "react-router-dom";
import { fetchDailyBriefing } from "../api/decision";
import { Card, FetchGate } from "../components/Card";
import { fmtDate } from "../format";
import { useFetch } from "../hooks/useFetch";

export function BriefingPage() {
  const state = useFetch(() => fetchDailyBriefing(20), []);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>每日简报</h1>
          <p className="page-lead">
            聚合已发布资金结论 / 三链交集 why / 形态突破观察 — 不产生新市场事实。
          </p>
        </div>
      </div>
      <Card title="简报（只读 serve 砖）">
        <FetchGate state={state}>
          {(d) => (
            <>
              {d.status !== "ok" ? (
                <div className="state-hint">
                  输入砖不可信或过期（{d.reason ?? d.status}）— 不生成叙事。
                  <div className="muted" style={{ marginTop: "0.5rem" }}>
                    moneyflow={d.inputs.moneyflow.trust}
                    {" · "}intersection={d.inputs.intersection.trust}
                    {" · "}screener={d.inputs.screener.trust}
                  </div>
                </div>
              ) : (
                <>
                  <p className="page-lead">
                    as-of {d.as_of ? fmtDate(d.as_of) : "—"} · horizon {d.horizon}
                  </p>
                  <p className="assist-conclusion">{d.narrative}</p>
                  {d.sections.map((sec) => (
                    <details key={sec.id} className="dossier-l2" open={(sec.count ?? 0) > 0}>
                      <summary>
                        {sec.title} · {sec.count ?? sec.items.length}
                      </summary>
                      {sec.items.length === 0 ? (
                        <div className="state-hint">本节诚实空态</div>
                      ) : (
                        <ul className="briefing-list">
                          {sec.items.map((item, i) => (
                            <li key={`${sec.id}-${i}`}>
                              {item.stock_code ? (
                                <Link to={`/stock/${item.stock_code}`}>
                                  {item.stock_name ?? item.stock_code}
                                </Link>
                              ) : null}
                              {item.sector_code ? (
                                <span className="mono muted"> {item.sector_code}</span>
                              ) : null}
                              <div className="assist-conclusion">{item.text}</div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </details>
                  ))}
                </>
              )}
              <p className="muted dossier-note">{d.disclaimer}</p>
            </>
          )}
        </FetchGate>
      </Card>
    </div>
  );
}
