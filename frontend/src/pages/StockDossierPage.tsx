/**
 * 股票档案 MVP — `#/stock/:code`
 * Decision-assist layers (basic / form·stage / holders / moneyflow). Separate from workbench.
 * NON-goal: Optuna, fake holder PnL, fake 机构 deep-link.
 */
import { FormEvent, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  fetchStockIntersection,
  fetchStockMoneyflow,
  type IntersectionStockResp,
  type StockMoneyflowResp,
} from "../api/decision";
import { fetchStockDossier, type StockDossierResponse, type StockFormStage } from "../api/stock";
import { Card, FetchGate } from "../components/Card";
import { FacetChipRow } from "../components/FacetChip";
import { chipsFromDossier } from "../facet/registry";
import { fmtInt, fmtPct } from "../format";
import { useFetch } from "../hooks/useFetch";

type TabKey = "overview" | "form" | "holders" | "moneyflow" | "intersection";

const TABS: { key: TabKey; label: string; enabled: boolean; soon?: string }[] = [
  { key: "overview", label: "概况", enabled: true },
  { key: "form", label: "形态·阶段", enabled: true },
  { key: "holders", label: "股东", enabled: true },
  { key: "moneyflow", label: "资金", enabled: true },
  { key: "intersection", label: "交集", enabled: true },
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
  const f = d.form_stage;
  const chips = chipsFromDossier({
    stockCode: d.stock_code,
    formName: f?.form_name,
    axisPos: f?.axis_pos,
    axisTrend: f?.axis_trend,
    axisPurity: f?.axis_purity,
    axisVol: f?.axis_vol,
    breakout: f?.is_breakout_event,
  });
  return (
    <>
      <p className="dossier-observation">
        {d.observation.text ?? "观察结论未知 — 形态/阶段砖块不足。"}
      </p>
      <div className="facet-chip-block">
        <span className="facet-chip-label">可探索 facet</span>
        <FacetChipRow facets={chips} emptyHint="暂无可跳转形态/轴标签" />
      </div>
      <div className="dossier-meta muted">
        <span>observation {d.observation.version}</span>
        <span>surface {d.surface}</span>
        {f?.trade_date && <span>form as-of {f.trade_date}</span>}
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
  const chips = chipsFromDossier({
    stockCode: props.d.stock_code,
    formName: f.form_name,
    axisPos: f.axis_pos,
    axisTrend: f.axis_trend,
    axisPurity: f.axis_purity,
    axisVol: f.axis_vol,
    breakout: f.is_breakout_event,
  });
  return (
    <>
      <div className="facet-chip-block">
        <span className="facet-chip-label">点标签 → 同形态/轴宇宙</span>
        <FacetChipRow facets={chips} />
      </div>
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
      <details className="dossier-gaps">
        <summary>明细 · resolver / hybrid</summary>
        <p className="muted dossier-note">{f.resolver_note}</p>
        {f.hybrid_residual_fields && f.hybrid_residual_fields.length > 0 && (
          <p className="muted dossier-note" title={(f.hybrid_residual_fields || []).join(", ")}>
            形态读模式：hybrid — accepted 覆盖 name/pos/trend/breakout；残差轴仍 fact（
            {f.hybrid_residual_fields.slice(0, 4).join(", ")}
            {f.hybrid_residual_fields.length > 4 ? "…" : ""}）— 非纯 accepted
          </p>
        )}
      </details>
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
            {cov.holders_episode_only
              ? ` · episode-only ${cov.holders_episode_only}（无假链）`
              : ""}
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
              <th>本股建仓/状态</th>
              <th>加减仓</th>
              <th>收益(α)</th>
            </tr>
          </thead>
          <tbody>
            {h.rows.map((r) => {
              const ep = r.episode;
              return (
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
                          title={
                            r.institution_link_status === "profile_low_sample"
                              ? "有画像但 low_sample（样本量不足）"
                              : "机构档案"
                          }
                        >
                          {r.holder_name}
                          {r.institution_profile_low_sample ? " ·低样本" : ""}
                        </Link>
                      ) : (
                        <span
                          className="holder-name"
                          title={
                            r.institution_link_status === "episode_only_no_profile"
                              ? "本股有 episode，mart 无画像行 — 不做假链接"
                              : "无机构档案 — 不做假链接"
                          }
                        >
                          {r.holder_name}
                          {r.institution_link_status === "episode_only_no_profile"
                            ? " ·无档案"
                            : ""}
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
                  <td className="mono" title="来自 fact_inst_episode（本股披露期状态机）">
                    {ep?.open_date ? `${ep.open_date} · ${ep.status ?? "—"}` : "—"}
                  </td>
                  <td className="mono">
                    {ep ? `+${ep.n_adds ?? 0}/-${ep.n_trims ?? 0}` : "—"}
                  </td>
                  <td
                    className="muted"
                    title="仅已了结(closed)且可测 episode 有收益；持有中/未测=未知，不假填"
                  >
                    {ep?.return_measured && ep.alpha_c1 != null
                      ? fmtPct(ep.alpha_c1)
                      : ep
                        ? "持有中/未测"
                        : "未知"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted dossier-note">
        连续期数为启发式披露期数；本股建仓/状态/加减仓来自机构 episode 状态机；收益(α)仅对已了结且可测
        episode 显示，持有中或未测标未知 — 宁缺勿假填 0。
        {h.episode_overlay && `（本期 ${h.episode_overlay.holders_with_episode}/${h.rows.length} 位有 episode）`}
      </p>
    </>
  );
}

function MoneyflowPanel(props: { code: string }) {
  const state = useFetch(() => fetchStockMoneyflow(props.code), [props.code]);
  return (
    <FetchGate state={state}>
      {(m: StockMoneyflowResp) => {
        const dc = m.planes.moneyflow_dc;
        const chips = chipsFromDossier({
          stockCode: props.code,
          behavior: m.behavior.behavior,
          behaviorZh: m.behavior.behavior_zh,
        });
        return (
          <>
            <p className="dossier-observation">
              {m.conclusion ?? "本股资金结论未知 — 窗未满、分母缺失或板块未形成行为标签。"}
            </p>
            <div className="facet-chip-block">
              <span className="facet-chip-label">点行为 → 同行为板块宇宙（Cap A board）</span>
              <FacetChipRow facets={chips} emptyHint="行为未形成 — 不可跳转" />
            </div>
            <div className="dossier-meta muted">
              <span>{m.behavior.behavior_zh}</span>
              <span>{m.behavior_version}</span>
              {m.sector_context?.sector_name && (
                <span>
                  板块 {m.sector_context.sector_name} · {m.sector_context.flow_regime ?? "—"}
                </span>
              )}
              {dc.as_of && <span>DC as-of {dc.as_of}</span>}
            </div>
            <details open className="dossier-l2">
              <summary>L2 · 多窗相对流入（东财面）</summary>
              <h3 className="dossier-subhead">{dc.label}</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>窗口</th>
                      <th>状态</th>
                      <th>相对比率</th>
                      <th>窗口涨跌</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dc.horizons.map((h) => (
                      <tr key={h.horizon}>
                        <td className="mono">{h.horizon}日</td>
                        <td>{h.status === "known" ? "已知" : "未知"}</td>
                        <td className="mono">
                          {h.relative_ratio_pct != null ? `${h.relative_ratio_pct.toFixed(2)}%` : "—"}
                        </td>
                        <td className="mono">
                          {h.window_return_pct != null ? `${h.window_return_pct.toFixed(2)}%` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
            <p className="muted dossier-note">{m.disclaimer}</p>
            {m.gaps.length > 0 && (
              <details className="dossier-gaps">
                <summary>L3 · 已知缺口 ({m.gaps.length})</summary>
                <ul>
                  {m.gaps.map((g) => (
                    <li key={g} className="mono">
                      {g}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        );
      }}
    </FetchGate>
  );
}

function IntersectionPanel(props: { code: string }) {
  const state = useFetch(() => fetchStockIntersection(props.code), [props.code]);
  return (
    <FetchGate state={state}>
      {(m: IntersectionStockResp) => {
        if (m.status === "stale") {
          return (
            <div className="state-hint">
              交集数据过期或链间 as-of 不一致（{m.reason}）— 不展示假交集，等待下一轮更新。
            </div>
          );
        }
        if (!m.in_intersection || !m.detail) {
          return (
            <div className="state-hint">
              当前窗口本股不在交集最强榜（{m.reason ?? "未同属三链强势扇区"}）— 诚实空态，非故障。
            </div>
          );
        }
        const d = m.detail;
        const chips = chipsFromDossier({
          stockCode: props.code,
          inIntersection: true,
        });
        return (
          <>
            <p className="dossier-observation">{d.why}</p>
            <div className="facet-chip-block">
              <FacetChipRow facets={chips} />
            </div>
            <div className="dossier-meta muted">
              <span>as-of 东财 {m.as_of.dc_industry ?? "—"}</span>
              <span>概念 {m.as_of.dc_concept ?? "—"}</span>
              <span>申万 {m.as_of.sw_industry ?? "—"}</span>
            </div>
            <h3 className="dossier-subhead">强势东财行业链</h3>
            <ul>
              {d.industry_sectors.map((s) => (
                <li key={s.sector_code}>
                  {s.sector_name ?? s.sector_code} · {s.behavior_zh}
                </li>
              ))}
            </ul>
            <h3 className="dossier-subhead">强势概念链</h3>
            <ul>
              {d.concept_sectors.map((s) => (
                <li key={s.sector_code}>
                  {s.sector_name ?? s.sector_code} · {s.behavior_zh}
                </li>
              ))}
            </ul>
            <h3 className="dossier-subhead">强势申万行业链</h3>
            <ul>
              {(d.sw_sectors ?? []).map((s) => (
                <li key={s.sector_code}>
                  {s.sector_name ?? s.sector_code} · {s.behavior_zh}
                </li>
              ))}
            </ul>
            <p className="muted dossier-note">{m.disclaimer}</p>
          </>
        );
      }}
    </FetchGate>
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
            ← 市场
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
            onClick={() => t.enabled && setTab(t.key)}
            disabled={!t.enabled}
            title={t.enabled ? undefined : `即将：${t.soon}`}
          >
            {t.label}
            {!t.enabled && <span className="tab-soon"> · 即将</span>}
          </button>
        ))}
      </div>

      <Card title={TABS.find((t) => t.key === tab)?.label ?? ""}>
        {tab === "moneyflow" || tab === "intersection" ? (
          /^\d{6}$/.test(code) ? (
            tab === "moneyflow" ? (
              <MoneyflowPanel code={code} />
            ) : (
              <IntersectionPanel code={code} />
            )
          ) : (
            <div className="state-hint">请输入 6 位股票代码</div>
          )
        ) : (
          <FetchGate state={state}>
            {(d) => {
              if (tab === "overview") return <OverviewPanel d={d} />;
              if (tab === "form") return <FormPanel d={d} />;
              return <HoldersPanel d={d} />;
            }}
          </FetchGate>
        )}
      </Card>
    </div>
  );
}
