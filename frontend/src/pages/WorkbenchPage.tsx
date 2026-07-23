/** 工作台 — 手动 ops 触发面（ops_manual_run「前端按钮面板」）。
 *  Tab「一键更新」= daily_update 主路径 + current_activity 可观测性。
 *  Tab「分步节点」= Capability E step cards（真实 pipeline/derive jobs；无 safe API 则 disabled+reason）。
 *  不堆资金流决策面；不发明第二编排 DAG。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
  classifyLogLine,
  deriveActivityFallback,
  fetchOpsJob,
  fetchPipelineNodes,
  formatDuePlanAsOf,
  formatLogMtime,
  jobLooksActive,
  nodeActivityView,
  nodeCardTone,
  nodeLooksActive,
  nodeProgressPct,
  overallProgressPct,
  PIPELINE_PHASE_ORDER,
  runLandAccept,
  runOpsJob,
  type DeltaManifestPreview,
  type LandAcceptParams,
  type OpsJobStatus,
  type PipelineNode,
} from "../api/ops";
import { Card } from "../components/Card";

const DAILY_UPDATE_JOB = "daily_update";
const POLL_MS = 2500;

type WorkbenchTab = "oneclick" | "steps";
type LandMode = LandAcceptParams["mode"];
type LandDomain = LandAcceptParams["domain"];

function activeAlertNames(flags: Record<string, boolean> | undefined): string[] {
  if (!flags) return [];
  return Object.entries(flags)
    .filter(([, on]) => on)
    .map(([name]) => name);
}

function statusLabel(s: OpsJobStatus | null): string {
  if (!s) return "未加载";
  const act = deriveActivityFallback(s);
  if (act?.summary) return act.summary;
  if (jobLooksActive(s)) {
    const owner = s.owner || "unknown";
    const pid = s.owner_pid != null ? ` pid=${s.owner_pid}` : "";
    return `运行中 · writer=${owner}${pid}`;
  }
  const alerts = activeAlertNames(s.alert_flags);
  if (alerts.length) return `空闲 · 有告警 flag（${alerts.length}）`;
  return "空闲";
}

function toneLabel(tone: ReturnType<typeof nodeCardTone>): string {
  switch (tone) {
    case "running":
      return "running";
    case "ok":
      return "ok";
    case "alert":
      return "alert";
    case "disabled":
      return "disabled";
    default:
      return "idle";
  }
}

function ProgressBar(props: {
  pct: number;
  label: string;
  live?: boolean;
  tone?: "live" | "ok" | "soft" | "fail" | "idle";
}) {
  const tone = props.tone ?? (props.live ? "live" : "idle");
  return (
    <div className={`ops-progress ops-progress-${tone}`}>
      <div className="ops-progress-head">
        <span>{props.label}</span>
        <span className="mono">{Math.round(props.pct)}%</span>
      </div>
      <div className="ops-progress-track" aria-hidden="true">
        <div
          className={`ops-progress-fill${props.live ? " ops-progress-pulse" : ""}`}
          style={{ width: `${Math.max(0, Math.min(100, props.pct))}%` }}
        />
      </div>
    </div>
  );
}

function PhaseRail(props: { phase: string | null | undefined; active: boolean }) {
  const cur = props.phase ?? "idle";
  const idx = PIPELINE_PHASE_ORDER.indexOf(cur as (typeof PIPELINE_PHASE_ORDER)[number]);
  const finished =
    !props.active &&
    (cur === "ok" || cur === "soft_waiting" || cur === "fail" || cur === "alert");
  return (
    <div className="ops-phase-rail" aria-label="pipeline phases">
      {PIPELINE_PHASE_ORDER.map((id, i) => {
        const here = props.active && cur === id;
        const done = finished || (idx >= 0 && i < idx) || (!props.active && idx >= 0 && i <= idx);
        return (
          <span
            key={id}
            className={`ops-phase-chip${done ? " done" : ""}${here ? " here" : ""}`}
          >
            {id}
          </span>
        );
      })}
    </div>
  );
}

function formatAdvancedPart(p: Record<string, unknown> | string): string {
  if (typeof p === "string") return p;
  const domain = String(p.domain ?? p.data_domain ?? p.name ?? "?");
  const action = p.action != null ? String(p.action) : p.status != null ? String(p.status) : "";
  return action ? `${domain}:${action}` : domain;
}

function DeltaManifestCard(props: { manifest: DeltaManifestPreview | null | undefined; live?: boolean }) {
  const m = props.manifest;
  if (!m || typeof m !== "object") return null;
  if (m.raw && !m.delta && m.advanced_n == null && !m.process_plan) {
    return (
      <div className="ops-delta">
        <div className="ops-delta-label">
          delta_manifest{props.live ? "（live）" : ""} · 未解析 JSON
        </div>
        <pre className="ops-delta-body mono">{String(m.raw).slice(0, 400)}</pre>
      </div>
    );
  }

  // Full report: nested delta.advanced_partitions; live: advanced_n + formal[].
  const nestedAdv = Array.isArray(m.delta?.advanced_partitions)
    ? m.delta!.advanced_partitions!
    : [];
  const advancedLabels = nestedAdv.map(formatAdvancedPart);
  const advancedN =
    typeof m.advanced_n === "number"
      ? m.advanced_n
      : advancedLabels.length > 0
        ? advancedLabels.length
        : null;

  const planObj =
    m.process_plan && typeof m.process_plan === "object" && !Array.isArray(m.process_plan)
      ? m.process_plan
      : null;
  const planLines: string[] = [];
  if (planObj) {
    for (const [step, raw] of Object.entries(planObj)) {
      if (step === "state_change_force" || step === "any_state_changed") continue;
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        const r = raw as { action?: string; reason?: string };
        planLines.push(`${step}:${r.action ?? "?"}${r.reason ? `(${r.reason})` : ""}`);
      }
    }
  } else if (m.dc && typeof m.dc === "object") {
    planLines.push(`dc_industry_view:${m.dc.action ?? "?"}${m.dc.reason ? `(${m.dc.reason})` : ""}`);
  }

  const stateSrc = m.delta?.state_changes ?? m.state_changes;
  const anyChanged =
    stateSrc && typeof stateSrc === "object"
      ? Boolean((stateSrc as { any_changed?: boolean }).any_changed)
      : null;
  const latePolicy =
    m.delta?.late_window_policy ?? m.late_window_policy ?? null;
  const timing = m.stage_timing_s && typeof m.stage_timing_s === "object" ? m.stage_timing_s : null;
  const budget = m.budget_status && typeof m.budget_status === "object" ? m.budget_status : null;
  const formal = Array.isArray(m.formal) ? m.formal : [];
  const incremental =
    Array.isArray(m.incremental) && m.incremental.length > 0
      ? m.incremental
      : Array.isArray(m.acquire_summary?.incremental)
        ? m.acquire_summary!.incremental!
        : [];

  return (
    <div className="ops-delta">
      <div className="ops-delta-label">
        增量识别{props.live ? "（live）" : "（上次报告）"} · 非 in-flight 进度表
      </div>
      <div className="ops-delta-body mono">
        {incremental.length > 0 && (
          <div>
            period_incremental:{" "}
            {incremental
              .slice(0, 4)
              .map(
                (r) =>
                  `${r.domain ?? "?"}:${r.action ?? r.status ?? "?"}` +
                  (r.report_date ? `(${r.report_date})` : ""),
              )
              .join(", ")}
          </div>
        )}
        {advancedN != null && advancedN > 0 ? (
          <div>
            advanced_n={advancedN}
            {advancedLabels.length > 0
              ? ` · ${advancedLabels.slice(0, 8).join(", ")}`
              : formal.length > 0
                ? ` · formal ${formal
                    .slice(0, 6)
                    .map((f) => `${f.domain ?? "?"}:${f.action ?? "?"}`)
                    .join(", ")}`
                : ""}
          </div>
        ) : advancedN === 0 ? (
          <div>advanced_n=0（无分区前进）</div>
        ) : (
          <div>advanced: 未知（报告无 advanced 字段）</div>
        )}
        {latePolicy != null && <div>late_window: {String(latePolicy)}</div>}
        {anyChanged != null && <div>state_changes.any_changed: {String(anyChanged)}</div>}
        {planLines.length > 0 && <div>process: {planLines.slice(0, 8).join(" · ")}</div>}
        {timing && (
          <div>
            timing_s:{" "}
            {Object.entries(timing)
              .slice(0, 6)
              .map(([k, v]) => `${k}=${v}`)
              .join(" · ")}
          </div>
        )}
        {budget && (
          <div>
            budget:{" "}
            {Object.entries(budget)
              .slice(0, 6)
              .map(([k, v]) => `${k}=${v}`)
              .join(" · ")}
          </div>
        )}
      </div>
    </div>
  );
}

function WaterfallLog(props: { lines: string[] }) {
  if (!props.lines.length) {
    return <div className="state-hint">尚无日志（未跑过或日志文件不存在）</div>;
  }
  return (
    <div className="ops-waterfall mono" role="log">
      {props.lines.map((line, i) => {
        const kind = classifyLogLine(line);
        return (
          <div key={`${i}-${line.slice(0, 24)}`} className={`ops-waterfall-line wf-${kind}`}>
            {line}
          </div>
        );
      })}
    </div>
  );
}

export function WorkbenchPage() {
  const [tab, setTab] = useState<WorkbenchTab>("oneclick");
  const [status, setStatus] = useState<OpsJobStatus | null>(null);
  const [nodes, setNodes] = useState<PipelineNode[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [triggeringJob, setTriggeringJob] = useState<string | null>(null);
  const [polledAt, setPolledAt] = useState<number | null>(null);
  const [landDomain, setLandDomain] = useState<LandDomain>("daily");
  const [landMode, setLandMode] = useState<LandMode>("land_then_accept");
  const [landStart, setLandStart] = useState("");
  const [landEnd, setLandEnd] = useState("");
  const [landBatchId, setLandBatchId] = useState("");
  const [landFromRaw, setLandFromRaw] = useState(false);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, catalog] = await Promise.all([
        fetchOpsJob(DAILY_UPDATE_JOB),
        fetchPipelineNodes(),
      ]);
      setStatus(s);
      setNodes(catalog.nodes ?? []);
      setPolledAt(Date.now());
      setLoadError(null);
      return s;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setLoadError(msg);
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyNodeActive = nodes.some((n) => nodeLooksActive(n.status));
  const chainActive = jobLooksActive(status) || anyNodeActive;

  useEffect(() => {
    if (!chainActive) {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current != null) return;
    pollRef.current = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [chainActive, refresh]);

  const onRunDaily = async () => {
    setTriggering(true);
    setTriggeringJob(DAILY_UPDATE_JOB);
    setActionError(null);
    setActionMsg(null);
    try {
      const resp = await runOpsJob(DAILY_UPDATE_JOB);
      setActionMsg(`已受理 pid=${resp.pid} — 下方会显示当前阶段；预检失败会写 ALERT flag`);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        setActionError(e.message);
      } else {
        setActionError(e instanceof Error ? e.message : String(e));
      }
      await refresh();
    } finally {
      setTriggering(false);
      setTriggeringJob(null);
    }
  };

  const onRunNode = async (job: string) => {
    setTriggering(true);
    setTriggeringJob(job);
    setActionError(null);
    setActionMsg(null);
    try {
      const resp = await runOpsJob(job);
      setActionMsg(`节点 ${job} 已受理 pid=${resp.pid}`);
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        setActionError(e.message);
      } else {
        setActionError(e instanceof Error ? e.message : String(e));
      }
      await refresh();
    } finally {
      setTriggering(false);
      setTriggeringJob(null);
    }
  };

  const onRunLandAccept = async () => {
    setTriggering(true);
    setTriggeringJob("sync_land_accept");
    setActionError(null);
    setActionMsg(null);
    const params: LandAcceptParams = {
      domain: landDomain,
      mode: landMode,
    };
    if (landMode === "accept_from_landing") {
      params.batch_id = landBatchId.trim();
    } else {
      params.start = landStart.trim();
      params.end = landEnd.trim();
      if (landFromRaw) params.from_local_raw = true;
    }
    try {
      const resp = await runLandAccept(params);
      setActionMsg(
        `S1/S2 ${resp.domain}/${resp.mode} 已受理 pid=${resp.pid}`,
      );
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) {
        setActionError(e.message);
      } else {
        setActionError(e instanceof Error ? e.message : String(e));
      }
      await refresh();
    } finally {
      setTriggering(false);
      setTriggeringJob(null);
    }
  };

  const busy = triggering || chainActive;
  const writerBusy = Boolean(status?.writer_busy);
  const alerts = activeAlertNames(status?.alert_flags);
  const tail = status?.log_tail ?? [];
  const activity = deriveActivityFallback(status);
  const runOutcome = status?.run_outcome ?? null;
  const softWaiting = runOutcome === "soft_waiting_clock";
  const integrityObserve = runOutcome === "integrity_observe";
  const hardFail = runOutcome === "hard_fail";
  // Soft/integrity must never surface as red FAIL / 阻断 (plan §C2 + closed-loop).
  const blocking =
    hardFail
      ? status?.alert_summary ||
        activity?.blocking_reason ||
        (alerts.length ? `flag: ${alerts.join(", ")}` : "hard_fail")
      : softWaiting || integrityObserve
        ? null
        : activity?.blocking_reason ||
          (runOutcome ? null : status?.alert_summary) ||
          (!runOutcome && alerts.length ? `flag: ${alerts.join(", ")}` : null);

  const activityTone = jobLooksActive(status)
    ? "ops-activity ops-activity-live"
    : hardFail
      ? "ops-activity ops-activity-fail"
      : softWaiting
        ? "ops-activity ops-activity-soft"
        : integrityObserve
          ? "ops-activity ops-activity-soft"
          : blocking
            ? "ops-activity ops-activity-alert"
            : runOutcome === "success"
              ? "ops-activity ops-activity-ok"
              : "ops-activity";

  const live = jobLooksActive(status);
  const overallPct = overallProgressPct(activity?.phase, live, runOutcome);
  const progressTone = live
    ? "live"
    : hardFail
      ? "fail"
      : softWaiting || integrityObserve
        ? "soft"
        : runOutcome === "success"
          ? "ok"
          : "idle";
  const deltaManifest =
    (live ? status?.delta_manifest_live : status?.delta_manifest) ??
    status?.delta_manifest ??
    status?.delta_manifest_live;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>工作台</h1>
          <p className="state-hint" style={{ textAlign: "left", padding: "4px 0 0" }}>
            手动触发数据底座链。一键全链优先；卡住时用分步节点独立重跑（真实 CLI/API，无假阶段）。
          </p>
        </div>
        <div className="mark-ctl">
          {actionMsg && <span className="mark-msg">{actionMsg}</span>}
          <button className="btn btn-primary" onClick={() => void onRunDaily()} disabled={busy}>
            {triggering && triggeringJob === DAILY_UPDATE_JOB
              ? "提交中…"
              : chainActive
                ? "更新进行中…"
                : "数据更新"}
          </button>
        </div>
      </div>

      <div className="tab-group" style={{ marginBottom: 12 }}>
        <button
          type="button"
          className={`btn tab${tab === "oneclick" ? " active" : ""}`}
          onClick={() => setTab("oneclick")}
        >
          一键更新
        </button>
        <button
          type="button"
          className={`btn tab${tab === "steps" ? " active" : ""}`}
          onClick={() => setTab("steps")}
        >
          分步节点
        </button>
      </div>

      {loadError && (
        <div className="banner-warn">状态加载失败: {loadError}</div>
      )}
      {actionError && (
        <div className="banner-warn">触发失败: {actionError}</div>
      )}
      {softWaiting && !jobLooksActive(status) && tab === "oneclick" && (
        <div className="banner-soft">
          最近一次已结束 · 结果=等时钟/软观测（非 FAIL，也非仍在跑）:{" "}
          {status?.run_outcome_label || "soft_waiting_clock"}
          {status?.run_outcome_reason ? ` · ${status.run_outcome_reason}` : ""}
          {status?.report_date ? ` · report=${status.report_date}` : ""}
          {alerts.length > 0
            ? ` （上次跑留下的观测 flag: ${alerts.join(", ")} — 非硬阻断；成功跑会自清，或手工 rm /tmp/…）`
            : null}
        </div>
      )}
      {integrityObserve && !jobLooksActive(status) && tab === "oneclick" && (
        <div className="banner-soft">
          最近一次已结束 · 结果=完整性观测（非时钟、非硬 FAIL）:{" "}
          {status?.run_outcome_label || "integrity_observe"}
          {status?.run_outcome_reason ? ` · ${status.run_outcome_reason}` : ""}
          {status?.report_date ? ` · report=${status.report_date}` : ""}
          {alerts.length > 0
            ? ` （观测 flag: ${alerts.join(", ")} — 需修数据/派生，不是等发布窗）`
            : null}
        </div>
      )}
      {hardFail && !jobLooksActive(status) && tab === "oneclick" && (
        <div className="banner-fail">
          硬失败: {blocking}
          {alerts.length > 0
            ? ` （${alerts.join(", ")} — 修好后成功跑会自清，或手工清 /tmp flag）`
            : null}
        </div>
      )}
      {!softWaiting && !hardFail && blocking && !jobLooksActive(status) && tab === "oneclick" && (
        <div className="banner-warn">
          阻断 / 告警: {blocking}
          {alerts.length > 0
            ? ` （${alerts.join(", ")} — 修好后成功跑会自清，或手工清 /tmp flag）`
            : null}
        </div>
      )}
      {blocking && jobLooksActive(status) && tab === "oneclick" && (
        <div className="banner-info">
          注意: 仍有历史 ALERT flag（{blocking}）。当前链已在跑；成功结束通常会自清。
        </div>
      )}

      {tab === "oneclick" ? (
        <Card
          title="数据更新"
          extra={<span className="mono">{statusLabel(status)}</span>}
        >
          <div className={activityTone}>
            <div className="ops-activity-label">当前活动</div>
            <div className="ops-activity-summary">{activity?.summary ?? "—"}</div>
            {activity?.progress_line && (
              <div className="ops-activity-progress mono">{activity.progress_line}</div>
            )}
            <ProgressBar
              pct={overallPct}
              label={live ? "全链进度（阶段推算）" : "全链进度（上次结果）"}
              live={live}
              tone={progressTone}
            />
            <PhaseRail phase={activity?.phase} active={live} />
            <div className="ops-activity-meta mono">
              阶段={activity?.phase_label ?? "—"}
              {" · "}
              run_outcome={runOutcome ?? "—"}
              {status?.report_date ? ` · report=${status.report_date}` : ""}
              {" · "}
              日志更新={formatLogMtime(status?.log_mtime ?? null)}
              {activity?.log_age_s != null ? `（${Math.floor(activity.log_age_s)}s 前）` : ""}
              {" · "}
              轮询={polledAt ? new Date(polledAt).toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
              {live && status?.owner ? ` · writer=${status.owner}` : ""}
              {live && status?.owner_pid != null ? ` pid=${status.owner_pid}` : ""}
            </div>
          </div>

          <DeltaManifestCard manifest={deltaManifest} live={Boolean(live && status?.delta_manifest_live)} />

          <div className="kpi-grid" style={{ marginBottom: 10 }}>
            <div className="kpi">
              <label>任务</label>
              <b className="mono">{status?.job ?? DAILY_UPDATE_JOB}</b>
            </div>
            <div className="kpi">
              <label>说明</label>
              <b>{status?.label ?? "—"}</b>
            </div>
            <div className="kpi">
              <label>日志</label>
              <b className="mono" style={{ fontSize: 11 }}>
                {status?.log_path ?? "—"}
              </b>
            </div>
          </div>

          <div className="ops-due-plan">
            <div className="ops-due-plan-label">
              到期计划（{status?.due_plan?.label || "SLA 水位快照"} · 非 planner 裁决 · 非 in-flight 进度）
              {status?.due_plan?.snapshot_kind ? (
                <span className="mono"> · kind={status.due_plan.snapshot_kind}</span>
              ) : null}
              {status?.due_plan?.as_of ? (
                <span className="mono"> · as_of={formatDuePlanAsOf(status.due_plan.as_of)}</span>
              ) : null}
              {status?.due_plan?.source ? (
                <span className="mono"> · src={status.due_plan.source}</span>
              ) : null}
            </div>
            {!status?.due_plan?.items?.length ? (
              <div className="state-hint">暂无落后域，或尚无 watermark SLA 文件</div>
            ) : (
              <pre className="ops-due-plan-list mono">
                {status.due_plan.items
                  .map((row) => {
                    if (row.kind === "period_incremental") {
                      return (
                        `${row.domain.padEnd(18)} period=${String(row.watermark ?? "—")}  ` +
                        `${row.will_fetch ? "will-fetch" : "skip-current"}  ` +
                        `${row.action ?? "?"}  ` +
                        `${row.detail ?? ""}`
                      );
                    }
                    return (
                      `${row.domain.padEnd(18)} wm=${String(row.watermark ?? "—")}  ` +
                      `days_ago=${row.days_ago}  ` +
                      `${row.will_fetch ? "will-fetch≈all-due" : "on_demand/formal"}`
                    );
                  })
                  .join("\n")}
              </pre>
            )}
          </div>

          <div className="ops-log-head">
            <span>瀑布日志</span>
            <button className="btn" onClick={() => void refresh()} disabled={triggering}>
              刷新状态
            </button>
          </div>
          <WaterfallLog lines={tail} />
        </Card>
      ) : (
        <Card
          title="模块化分步节点"
          extra={
            <button className="btn" onClick={() => void refresh()} disabled={triggering}>
              刷新
            </button>
          }
        >
          <p className="state-hint" style={{ textAlign: "left", padding: "0 0 10px" }}>
            对齐真实 `chunkyctl pipeline|derive|sync`；S1/S2 用下方参数表单（白名单 daily/stock_st，≤40d）。
            writer 占用时独立运行会 409。
          </p>
          <div className="ops-step-flow">
            {nodes.map((node, idx) => {
              const tone = node.parameterized
                ? nodeLooksActive(node.status)
                  ? "running"
                  : "idle"
                : nodeCardTone(node);
              const act = nodeActivityView(node.status);
              const nodeBusy = nodeLooksActive(node.status);
              const canRun =
                node.runnable &&
                Boolean(node.job) &&
                !node.parameterized &&
                !busy &&
                !writerBusy;
              const canLand =
                node.parameterized &&
                !busy &&
                !writerBusy &&
                (landMode === "accept_from_landing"
                  ? Boolean(landBatchId.trim())
                  : Boolean(landStart.trim() && landEnd.trim()));
              return (
                <div key={node.id} className="ops-step-item">
                  {idx > 0 && <div className="ops-step-arrow" aria-hidden="true">→</div>}
                  <div className={`ops-step-card ops-step-${tone}`}>
                    <div className="ops-step-head">
                      <span className="ops-step-title">{node.label}</span>
                      <span className={`ops-step-badge ops-step-badge-${tone}`}>
                        {toneLabel(tone)}
                      </span>
                    </div>
                    <div className="ops-step-desc">{node.description}</div>
                    <div className="ops-step-activity mono">
                      {node.runnable
                        ? act?.summary ?? "空闲 · 尚无日志"
                        : node.disabled_reason ?? "不可独立运行"}
                    </div>
                    {node.runnable && (
                      <ProgressBar
                        pct={nodeProgressPct(tone)}
                        label={nodeBusy ? "节点进度" : "节点状态"}
                        live={nodeBusy}
                        tone={
                          nodeBusy
                            ? "live"
                            : tone === "alert"
                              ? "fail"
                              : tone === "ok"
                                ? "ok"
                                : "idle"
                        }
                      />
                    )}
                    <div className="ops-step-meta mono">
                      {node.job ? `job=${node.job}` : "job=—"}
                      {node.status?.log_mtime != null
                        ? ` · 最近 ${formatLogMtime(node.status.log_mtime)}`
                        : ""}
                      {nodeBusy && node.status?.owner
                        ? ` · writer=${node.status.owner}`
                        : ""}
                    </div>
                    {node.parameterized ? (
                      <div className="ops-land-form">
                        <label>
                          domain
                          <select
                            value={landDomain}
                            onChange={(e) => setLandDomain(e.target.value as LandDomain)}
                            disabled={busy}
                          >
                            <option value="daily">daily</option>
                            <option value="stock_st">stock_st</option>
                          </select>
                        </label>
                        <label>
                          mode
                          <select
                            value={landMode}
                            onChange={(e) => setLandMode(e.target.value as LandMode)}
                            disabled={busy}
                          >
                            <option value="land_then_accept">land_then_accept</option>
                            <option value="land_only">land_only (S1)</option>
                            <option value="accept_from_landing">accept_from_landing (S2)</option>
                          </select>
                        </label>
                        {landMode === "accept_from_landing" ? (
                          <label className="ops-land-wide">
                            batch-id
                            <input
                              className="mono"
                              value={landBatchId}
                              onChange={(e) => setLandBatchId(e.target.value)}
                              placeholder="landing batch id"
                              disabled={busy}
                            />
                          </label>
                        ) : (
                          <>
                            <label>
                              start
                              <input
                                className="mono"
                                value={landStart}
                                onChange={(e) => setLandStart(e.target.value)}
                                placeholder="YYYYMMDD"
                                disabled={busy}
                              />
                            </label>
                            <label>
                              end
                              <input
                                className="mono"
                                value={landEnd}
                                onChange={(e) => setLandEnd(e.target.value)}
                                placeholder="YYYYMMDD"
                                disabled={busy}
                              />
                            </label>
                            <label className="ops-land-check">
                              <input
                                type="checkbox"
                                checked={landFromRaw}
                                onChange={(e) => setLandFromRaw(e.target.checked)}
                                disabled={busy}
                              />
                              from-local-raw
                            </label>
                          </>
                        )}
                        <button
                          type="button"
                          className="btn"
                          disabled={!canLand}
                          title="POST /api/v3/ops/pipeline/land-accept/run"
                          onClick={() => void onRunLandAccept()}
                        >
                          {triggeringJob === "sync_land_accept"
                            ? "提交中…"
                            : nodeBusy
                              ? "运行中…"
                              : "按参数运行"}
                        </button>
                      </div>
                    ) : (
                      <div className="ops-step-actions">
                        {node.runnable && node.job ? (
                          <button
                            type="button"
                            className="btn"
                            disabled={!canRun}
                            title={
                              !canRun
                                ? writerBusy || busy
                                  ? "writer 占用或任务进行中"
                                  : undefined
                                : `POST /api/v3/ops/jobs/${node.job}/run`
                            }
                            onClick={() => void onRunNode(node.job!)}
                          >
                            {triggeringJob === node.job
                              ? "提交中…"
                              : nodeBusy
                                ? "运行中…"
                                : "独立运行"}
                          </button>
                        ) : (
                          <button type="button" className="btn" disabled title={node.disabled_reason ?? undefined}>
                            不可运行
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {nodes.length === 0 && (
            <div className="state-hint">节点目录未加载（API 需含 GET /api/v3/ops/pipeline/nodes）</div>
          )}
        </Card>
      )}
    </div>
  );
}
