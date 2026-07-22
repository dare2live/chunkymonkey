import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchProfile } from "../api/inst";
import type { InstProfileDim } from "../api/types";
import { Card, FetchGate } from "../components/Card";
import { EChart } from "../components/EChart";
import { fmtDate, fmtInt, fmtPct, pnlClass } from "../format";
import { useFetch } from "../hooks/useFetch";
import { UI } from "../theme";

/** 单机构 KPI 卡 (widget 独立取数)。 */
function KpiCard(props: { holder: string }) {
  const state = useFetch(() => fetchProfile(props.holder), [props.holder]);
  return (
    <Card title="总体表现">
      <FetchGate state={state}>
        {(p) => (
          <>
            {p.low_sample && (
              <div className="banner-warn">样本不足 (已了结 episode &lt; 10), 指标仅供参考, 不进正式排名</div>
            )}
            <div className="kpi-grid">
              <div className="kpi">
                <label>类型</label>
                <b>{p.holder_type ?? "—"}</b>
              </div>
              <div className="kpi">
                <label>已了结 episode</label>
                <b>{fmtInt(p.n_closed)}</b>
              </div>
              <div className="kpi">
                <label>超额中位 (vs HS300)</label>
                <b className={pnlClass(p.median_alpha)}>{fmtPct(p.median_alpha)}</b>
              </div>
              <div className="kpi">
                <label>平均超额</label>
                <b className={pnlClass(p.avg_alpha)}>{fmtPct(p.avg_alpha)}</b>
              </div>
              <div className="kpi">
                <label>胜率 (超额&gt;0)</label>
                <b>{fmtPct(p.win_rate_alpha)}</b>
              </div>
              <div className="kpi">
                <label>收益中位</label>
                <b className={pnlClass(p.median_ret)}>{fmtPct(p.median_ret)}</b>
              </div>
              <div className="kpi">
                <label>平均持有天数</label>
                <b>{fmtInt(p.avg_hold_days)}</b>
              </div>
            </div>
          </>
        )}
      </FetchGate>
    </Card>
  );
}

const DIM_TABS: { key: InstProfileDim["dim_type"]; label: string }[] = [
  { key: "industry_pit", label: "行业(PIT)" },
  { key: "year", label: "年份" },
  { key: "holder_type", label: "类型" },
];
const METRICS = ["超额中位", "胜率(超额)", "样本数"] as const;

function heatmapOption(dims: InstProfileDim[]): EChartsOption {
  const rows = [...dims].sort((a, b) => (a.median_alpha ?? -Infinity) - (b.median_alpha ?? -Infinity));
  const yLabels = rows.map((d) => d.dim_value);
  // 每指标列独立归一做色阶 (量纲不同), tooltip/label 显原始值
  const cols: (number | null)[][] = [
    rows.map((d) => d.median_alpha),
    rows.map((d) => d.win_rate_alpha),
    rows.map((d) => d.n_closed),
  ];
  // 分向色阶 绿→白→红 (白=列中位): 深色端格子标签用白字, 浅色端用正文色
  const data: { value: [number, number, number, number | null]; label: { color: string } }[] = [];
  cols.forEach((col, x) => {
    const vals = col.filter((v): v is number => v !== null);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    col.forEach((v, y) => {
      const norm = v === null ? 0 : max > min ? (v - min) / (max - min) : 0.5;
      // 白字仅两个饱和极值端 (实测 WCAG: 纯红/纯绿上白字 ~4.4-4.9:1 优于深字 ~3.5-3.9:1)
      data.push({
        value: [x, y, norm, v],
        label: { color: norm >= 0.95 || norm <= 0.02 ? UI.bgPanel : UI.text },
      });
    });
  });
  const fmtCell = (x: number, raw: number | null) => {
    if (raw === null) return "—";
    return x === 2 ? String(Math.round(raw)) : `${(raw * 100).toFixed(1)}%`;
  };
  return {
    grid: { left: 110, right: 12, top: 8, bottom: 28 },
    xAxis: {
      type: "category",
      data: [...METRICS],
      axisLabel: { color: UI.textDim, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: "category",
      data: yLabels,
      axisLabel: { color: UI.textDim, width: 96, overflow: "truncate", fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    visualMap: {
      min: 0,
      max: 1,
      dimension: 2,
      show: false,
      inRange: { color: [UI.down, UI.bgPanel, UI.up] },
    },
    tooltip: {
      formatter: (p) => {
        const v = (p as unknown as { value: [number, number, number, number | null] }).value;
        return `${yLabels[v[1]]}<br/>${METRICS[v[0]]}: ${fmtCell(v[0], v[3])}`;
      },
    },
    series: [
      {
        type: "heatmap",
        data,
        label: {
          show: true,
          color: UI.text,
          formatter: (p) => {
            const v = p.value as [number, number, number, number | null];
            return fmtCell(v[0], v[3]);
          },
        },
        itemStyle: { borderColor: UI.bgPanel, borderWidth: 2 },
      },
    ],
  };
}

/** 维度表现热力图卡 (行业×指标, 可切 年份/类型 维度)。 */
function DimHeatmapCard(props: { holder: string }) {
  const state = useFetch(() => fetchProfile(props.holder), [props.holder]);
  const [dimType, setDimType] = useState<InstProfileDim["dim_type"]>("industry_pit");
  const dims = useMemo(
    () => (state.data?.dims ?? []).filter((d) => d.dim_type === dimType),
    [state.data, dimType],
  );
  const option = useMemo(() => heatmapOption(dims), [dims]);

  return (
    <Card
      title="维度表现热力图"
      extra={
        <div className="tab-group">
          {DIM_TABS.map((t) => (
            <button
              key={t.key}
              className={`btn tab${dimType === t.key ? " active" : ""}`}
              onClick={() => setDimType(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      <FetchGate state={state} empty={(p) => p.dims.length === 0} emptyHint="无维度数据">
        {() =>
          dims.length === 0 ? (
            <div className="state-hint">该维度无数据</div>
          ) : (
            <EChart option={option} height={Math.max(200, dims.length * 30 + 80)} />
          )
        }
      </FetchGate>
    </Card>
  );
}

/** episode 时间线表卡。 */
function EpisodesCard(props: { holder: string }) {
  const state = useFetch(() => fetchProfile(props.holder), [props.holder]);
  return (
    <Card title="episode 时间线 (最近 200 条)">
      <FetchGate state={state} empty={(p) => p.episodes.length === 0} emptyHint="无 episode 记录">
        {(p) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>标的</th>
                  <th>建仓期</th>
                  <th>退出期</th>
                  <th>状态</th>
                  <th>收益</th>
                  <th>超额</th>
                  <th>增持/减持</th>
                  <th>行业(PIT)</th>
                </tr>
              </thead>
              <tbody>
                {p.episodes.map((e) => (
                  <tr key={`${e.stock}|${e.open_date}|${e.status}`} className={e.seeded ? "low-sample" : ""}>
                    <td className="mono">
                      <Link to={`/stock/${e.stock}`}>{e.stock}</Link>
                    </td>
                    <td>
                      {fmtDate(e.open_date)}
                      {e.seeded && (
                        <span className="tag" title="非新进建仓 (数据窗口起点已在持仓), 收益口径不完整">
                          seeded
                        </span>
                      )}
                    </td>
                    <td>{fmtDate(e.close_date)}</td>
                    <td>{e.status === "holding" ? <span className="tag tag-hold">持有中</span> : "已了结"}</td>
                    <td className={pnlClass(e.ret_c1)}>{fmtPct(e.ret_c1)}</td>
                    <td className={pnlClass(e.alpha_c1)}>{fmtPct(e.alpha_c1)}</td>
                    <td>
                      {e.n_adds} / {e.n_trims}
                    </td>
                    <td>{e.sw_l1_at_open ?? "—"}</td>
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

export function InstitutionDetailPage() {
  const { holder } = useParams<{ holder: string }>();
  if (!holder) {
    return <div className="page">缺少机构名参数</div>;
  }
  return (
    <div className="page">
      <div className="page-head">
        <Link to="/institutions" className="back-link">
          ← 返回排名
        </Link>
        <h1 title={holder}>{holder}</h1>
      </div>
      <p className="page-lead">
        机构档案 · episode 与维度热力。标的代码可点进股票档案。
      </p>
      <p className="page-desc assist-disclaimer">
        诚实边界：本页只读已发布 holders/episode 画像；org_holding provider land 仍 BLOCKED
        （禁全市场 by-period mass re-pull）— 无 NOTICE_DATE 全景时不伪造机构 bulk 覆盖。
      </p>
      <KpiCard holder={holder} />
      <details open className="dossier-l2">
        <summary>L2 · 维度表现</summary>
        <DimHeatmapCard holder={holder} />
      </details>
      <details className="dossier-gaps">
        <summary>L3 · episode 明细</summary>
        <EpisodesCard holder={holder} />
      </details>
    </div>
  );
}
