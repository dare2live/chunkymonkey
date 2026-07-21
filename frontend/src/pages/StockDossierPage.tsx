/**
 * 股票档案 MVP — `#/stock/:code`
 * Decision-assist layers (basic / form·stage / holders). Separate from workbench.
 * NON-goal: moneyflow A, Optuna, fake holder PnL.
 */
import { FormEvent, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { fetchStockDossier, type StockDossierResponse, type StockFormStage } from "../api/stock";
import { Card, FetchGate } from "../components/Card";
import { fmtInt, fmtPct } from "../format";
import { useFetch } from "../hooks/useFetch";

type TabKey = "overview" | "form" | "holders";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "概况" },
  { key: "form", label: "形态·阶段" },
  { key: "holders", label: "股东" },
];

function industryLine(d: StockDossierResponse): string {
  const ind = d.basic.industry;
  if (!ind) return "行业未知";
  return [ind.l1_name, ind.l2_name, ind.l3_name].filter(Boolean).join(" › ") || "行业未知";
}

function AxisGrid(props: { form: StockFormStage }) {
  const f = props.form;
  const cells = [
    { label: "位置", value: f.axis_pos, memb: f.axis_pos_memb },
    { label: "趋势", value: f.axis_trend, memb: f.axis_trend_memb },
    { label: "纯度", value: f.axis_purity, memb: f.axis_purity_memb },
    { label: "量能", value: f.axis_vol, memb: f.axis_vol_memb },
  ];
  return (
    <div className="kpi-grid dossier-axis-grid">
      {cells.map((c) => (
        <div className="kpi" key={c.label}>
          <label>{c.label}</label>
          <b className="mono">{c.value ?? "—"}</b>
          <span className="kpi-sub">{c.memb == null ? "" : `memb ${c.memb.toFixed(2)}`}</span>
        </div>
      ))}
    </div>
  );
}

function OverviewPanel(props: { d: StockDossierResponse }) {
  const { d } = props;
  return (
    <>
      <p className="dossier-observation">
        {d.observation.text ?? "观察结论未知 — 形态/阶段砖块不足。"}
      </p>
      <div className="dossier-meta muted">
        <span>observation {d.observation.version}</span>
        <span>surface {d.surface}</span>
        {d.form_stage?.trade_date && <span>form as-of {d.form_stage.trade_date}</span>}
        {d.holders.report_date && <span>holders {d.holders.report_date}</span>}
      </div>
      {d.gaps.length > 0 && (
        <details className="dossier-gaps">
          <summary>已知缺口 ({d.gaps.length})</summary>
          <ul>
            {d.gaps.map((g) => (
              <li key={g} className="mono">
                {g}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function FormPanel(props: { d: StockDossierResponse }) {
  const f = props.d.form_stage;
  if (!f) return <div className="state-hint">无形态/阶段行</div>;
  return (
    <>
      <div className="kpi-grid">
        <div className="kpi">
          <label>形态</label>
          <b>{f.form_name ?? "—"}</b>
          <span className="kpi-sub">{f.form_sub ?? ""}</span>
        </div>
        <div className="kpi">
          <label>周 / 月</label>
          <b>
            {f.weekly_name ?? "—"} / {f.monthly_name ?? "—"}
          </b>
        </div>
        <div className="kpi">
          <label>突破事件</label>
          <b>{f.is_breakout_event ? "是" : "否"}</b>
        </div>
        <div className="kpi">
          <label>trade_date</label>
          <b className="mono">{f.trade_date}</b>
        </div>
      </div>
      <h3 className="dossier-subhead">阶段轴</h3>
      <AxisGrid form={f} />
      <p className="muted dossier-note">{f.resolver_note}</p>
    </>
  );
}

function HoldersPanel(props: { d: StockDossierResponse }) {
  const h = props.d.holders;
  if (!h.rows.length) return <div className="state-hint">无股东披露行</div>;
  const cov = h.institution_profile;
  return (
    <>
      <div className="dossier-meta muted">
        <span>报告期 {h.report_date ?? "—"}</span>
        {h.prev_report_date && <span>上期 {h.prev_report_date}</span>}
        <span>source {h.source ?? "—"}</span>
        {cov && cov.coverage != null && (
          <span title={cov.note}>
            机构档案覆盖 {cov.holders_with_profile}/{cov.holders_total}（
            {(cov.coverage * 100).toFixed(0)}%）
          </span>
        )}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>股东</th>
              <th>类型</th>
              <th>流通占比</th>
              <th>变化</th>
              <th>变动股数</th>
              <th>连续期数≈</th>
              <th>收益</th>
            </tr>
          </thead>
          <tbody>
            {h.rows.map((r) => (
              <tr key={`${r.holder_rank}-${r.holder_name}`}>
                <td className="mono">{r.holder_rank ?? "—"}</td>
                <td>
                  {r.holder_name_norm || r.holder_name ? (
                    r.has_institution_profile ? (
                      <Link
                        className="holder-name clickable"
                        to={`/institutions/${encodeURIComponent(
                          r.holder_name_norm || r.holder_name || "",
                        )}`}
                      >
                        {r.holder_name}
                      </Link>
                    ) : (
                      <span className="holder-name" title="无机构档案（覆盖~54%）— 不做假链接">
                        {r.holder_name}
                      </span>
                    )
                  ) : (
                    "—"
                  )}
                </td>
                <td>{r.holder_type ?? "—"}</td>
                <td className="mono">
                  {r.hold_ratio_float == null ? "—" : `${r.hold_ratio_float.toFixed(2)}%`}
                </td>
                <td>{r.change_status ?? "—"}</td>
                <td className="mono">{r.hold_change_num == null ? "—" : fmtInt(r.hold_change_num)}</td>
                <td className="mono">{r.approx_periods_present ?? "—"}</td>
                <td className="muted" title="未接 episode×价格引擎">
                  {r.return_pct == null ? "未知" : fmtPct(r.return_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted dossier-note">
        持仓周期仅启发式连续披露期数；收益/持有天数引擎未接 — 宁缺勿假填 0。
      </p>
    </>
  );
}

export function StockDossierPage() {
  const { code: routeCode } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const code = (routeCode || "").trim();
  const [tab, setTab] = useState<TabKey>("overview");
  const [draft, setDraft] = useState(code);

  const state = useFetch(
    () => {
      if (!/^\d{6}$/.test(code)) {
        return Promise.reject(new Error("请输入 6 位股票代码"));
      }
      return fetchStockDossier(code);
    },
    [code],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const next = draft.trim();
    if (/^\d{6}$/.test(next)) navigate(`/stock/${next}`);
  };

  const titleBits = useMemo(() => {
    const d = state.data;
    if (!d) return code || "股票档案";
    return `${d.basic.stock_name ?? "未知名"} · ${d.stock_code}`;
  }, [state.data, code]);

  return (
    <div className="dossier-page">
      <div className="page-head dossier-head">
        <div>
          <Link className="back-link" to="/market">
            ← 市场感知
          </Link>
          <h1>{titleBits}</h1>
          {state.data && <p className="dossier-crumb">{industryLine(state.data)}</p>}
        </div>
        <form className="dossier-lookup" onSubmit={onSubmit}>
          <input
            className="mono"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="6 位代码"
            maxLength={6}
            aria-label="股票代码"
          />
          <button className="btn" type="submit">
            打开
          </button>
        </form>
      </div>

      <div className="tab-group dossier-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`btn tab${tab === t.key ? " active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <Card title={TABS.find((t) => t.key === tab)?.label ?? ""}>
        <FetchGate state={state}>
          {(d) => {
            if (tab === "overview") return <OverviewPanel d={d} />;
            if (tab === "form") return <FormPanel d={d} />;
            return <HoldersPanel d={d} />;
          }}
        </FetchGate>
      </Card>
    </div>
  );
}
