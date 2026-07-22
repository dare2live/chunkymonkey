/** Ops manual jobs — backend routers/ops_manual_run.py (no auth beyond same-origin proxy). */

import { apiGet, apiPost } from "./client";

export interface OpsJobActivity {
  phase: string;
  phase_label: string;
  summary: string;
  progress_line: string | null;
  log_age_s: number | null;
  stale_log: boolean;
  blocking_reason: string | null;
}

export interface DuePlanItem {
  domain: string;
  watermark: string | null;
  days_ago: number;
  sla_days?: number | null;
  status?: string | null;
  will_fetch: boolean;
}

export interface DuePlanPreview {
  source: string | null;
  as_of: string | null;
  items: DuePlanItem[];
  snapshot_kind?: "preflight" | "post_acquire" | "latest" | null;
  label?: string | null;
  error?: string;
}

/** Typed daily_update outcome — SSOT from data/reports/daily_*.json (plan §C2). */
export type RunOutcome = "success" | "soft_waiting_clock" | "hard_fail";

export interface OpsJobStatus {
  job: string;
  label: string;
  running: boolean;
  writer_busy: boolean;
  owner: string | null;
  owner_pid: number | null;
  process_hint_running: boolean;
  alert_flags: Record<string, boolean>;
  alert_summary?: string | null;
  log_path: string;
  log_tail: string[];
  log_mtime: number | null;
  current_activity?: OpsJobActivity | null;
  due_plan?: DuePlanPreview | null;
  run_outcome?: RunOutcome | null;
  run_outcome_label?: string | null;
  run_outcome_reason?: string | null;
  report_path?: string | null;
  report_date?: string | null;
}

export interface OpsJobRunResp {
  job: string;
  accepted: boolean;
  pid: number;
}

/** Capability E step-card node from GET /api/v3/ops/pipeline/nodes */
export interface PipelineNode {
  id: string;
  label: string;
  description: string;
  job: string | null;
  runnable: boolean;
  parameterized?: boolean;
  disabled_reason: string | null;
  params_schema?: {
    domains: string[];
    modes: string[];
    requires: Record<string, string[]>;
    endpoint: string;
  };
  status: OpsJobStatus | null;
}

export interface PipelineNodesResp {
  primary_job: string;
  nodes: PipelineNode[];
}

export function fetchOpsJob(job: string): Promise<OpsJobStatus> {
  return apiGet<OpsJobStatus>(`/api/v3/ops/jobs/${encodeURIComponent(job)}`);
}

export function runOpsJob(job: string): Promise<OpsJobRunResp> {
  return apiPost<OpsJobRunResp>(`/api/v3/ops/jobs/${encodeURIComponent(job)}/run`);
}

export function fetchPipelineNodes(): Promise<PipelineNodesResp> {
  return apiGet<PipelineNodesResp>("/api/v3/ops/pipeline/nodes");
}

export interface LandAcceptParams {
  domain: "daily" | "stock_st";
  mode: "land_only" | "land_then_accept" | "accept_from_landing";
  start?: string;
  end?: string;
  batch_id?: string;
  from_local_raw?: boolean;
}

export interface LandAcceptRunResp extends OpsJobRunResp {
  argv: string[];
  mode: string;
  domain: string;
}

/** Capability E parameterized S1/S2 — not bare /jobs/.../run. */
export function runLandAccept(params: LandAcceptParams): Promise<LandAcceptRunResp> {
  return apiPost<LandAcceptRunResp>("/api/v3/ops/pipeline/land-accept/run", params);
}

/** True while flock or process hint says the chain is still alive. */
export function jobLooksActive(s: OpsJobStatus | null | undefined): boolean {
  if (!s) return false;
  return Boolean(s.writer_busy || s.process_hint_running || s.running);
}

/** Per-node activity: this job's process hint only (never global flock / running). */
export function nodeLooksActive(s: OpsJobStatus | null | undefined): boolean {
  if (!s) return false;
  return Boolean(s.process_hint_running);
}

/**
 * Sanitize API current_activity for step cards: never show「正在…」/ foreign pid
 * when this node is not actually running.
 */
export function nodeActivityView(s: OpsJobStatus | null | undefined): OpsJobActivity | null {
  if (!s) return null;
  const active = nodeLooksActive(s);
  const raw = deriveActivityFallback({
    ...s,
    // Global flock must not paint every card as the live chain.
    writer_busy: false,
    running: active,
    process_hint_running: active,
    // Drop foreign pid unless this node owns the process.
    owner: active ? s.owner : null,
    owner_pid: active ? s.owner_pid : null,
    current_activity: active
      ? s.current_activity
      : s.current_activity &&
          (s.current_activity.phase === "running" ||
            Boolean(s.current_activity.summary?.startsWith("正在")))
        ? null
        : s.current_activity,
  });
  return raw;
}

export function nodeCardTone(
  node: PipelineNode,
): "idle" | "running" | "ok" | "alert" | "disabled" {
  if (!node.runnable) return "disabled";
  const s = node.status;
  if (!s) return "idle";
  if (nodeLooksActive(s)) return "running";
  const act = nodeActivityView(s);
  if (act?.phase === "alert" || s.alert_summary) return "alert";
  if (act?.phase === "fail") return "alert";
  if (s.log_mtime != null && act && act.phase !== "idle") return "ok";
  return "idle";
}

/** Fallback when older API has no current_activity — parse log_tail locally. */
export function deriveActivityFallback(s: OpsJobStatus | null | undefined): OpsJobActivity | null {
  if (!s) return null;
  if (s.current_activity) return s.current_activity;

  const active = jobLooksActive(s);
  const tail = s.log_tail ?? [];
  let start = 0;
  for (let i = 0; i < tail.length; i++) {
    if (
      tail[i].includes("=== ChunkyMonkey daily update") ||
      tail[i].includes("=== ChunkyMonkey pipeline stage")
    ) {
      start = i;
    }
  }
  const run = tail.slice(start);
  const progress = [...run].reverse().find((l) => l.trim())?.trim() ?? "";
  const age =
    s.log_mtime != null ? Math.max(0, Date.now() / 1000 - s.log_mtime) : null;
  const stale = Boolean(active && age != null && age >= 90);

  let phase = "idle";
  let phaseLabel = "空闲";
  const checks: Array<[string, string, RegExp]> = [
    ["acquire", "① 获取 ACQUIRE", /①\s*获取|ACQUIRE/],
    ["clean", "② 清洗/派生 CLEAN", /②\s*清洗|CLEAN|land_then_accept/i],
    ["process", "③ 加工 PROCESS", /③\s*加工|PROCESS/],
    ["store", "④ 存储 STORE", /④\s*存储|post-acquire Store/i],
    ["preflight", "预检 preflight", /Preflight|Sync execution policy|Calendar foundation|Authorization/i],
    ["fail", "硬失败 / 阻断", /PREFLIGHT BLOCK|AUTH BLOCK|TIER0 BLOCK|WRITER BLOCK|FAIL rc=[2-5]|HARD_FAIL/i],
    ["soft_waiting", "已结束 · 等时钟/软观测", /soft_waiting_clock|SOFT_WAITING|pending_publish|DONE soft_waiting/i],
  ];
  for (const line of [...run].reverse()) {
    for (const [id, label, re] of checks) {
      if (active && (id === "fail" || id === "soft_waiting")) continue;
      if (re.test(line)) {
        phase = id;
        phaseLabel = label;
        break;
      }
    }
    if (phase !== "idle") break;
  }
  if (active && phase === "idle") {
    phase = "running";
    phaseLabel = "运行中";
  }

  let summary: string;
  let blocking: string | null = s.alert_summary ?? null;
  if (active) {
    summary = `正在: ${phaseLabel}`;
    if (s.owner) summary += ` · writer=${s.owner}`;
    if (s.owner_pid != null) summary += ` pid=${s.owner_pid}`;
    if (stale) summary += ` · 日志已 ${Math.floor(age || 0)}s 无新行（进程仍在）`;
  } else if (s.run_outcome === "hard_fail") {
    summary = `硬失败: ${s.run_outcome_label || "hard_fail"}`;
    phase = "fail";
    phaseLabel = "硬失败";
  } else if (s.run_outcome === "soft_waiting_clock") {
    summary = `最近一次已结束 · 结果=等时钟/软观测（${s.run_outcome_label || "soft_waiting_clock"}）`;
    phase = "soft_waiting";
    phaseLabel = "已结束 · 等时钟/软观测";
    blocking = null; // never paint soft wait as FAIL
  } else if (s.run_outcome === "success") {
    summary = "最近成功 · run_outcome=success";
    phase = "ok";
    phaseLabel = "成功";
    blocking = null;
  } else if (s.alert_summary) {
    summary = `告警: ${s.alert_summary}`;
    phase = "alert";
    phaseLabel = "告警残留";
  } else if (progress) {
    summary = `最近: ${progress.slice(0, 160)}`;
  } else {
    summary = "空闲 · 尚无日志";
  }

  return {
    phase,
    phase_label: phaseLabel,
    summary,
    progress_line: progress ? progress.slice(0, 320) : null,
    log_age_s: age != null ? Math.round(age * 10) / 10 : null,
    stale_log: stale,
    blocking_reason: blocking,
  };
}

export function formatLogMtime(mtime: number | null | undefined): string {
  if (mtime == null) return "—";
  try {
    return new Date(mtime * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return String(mtime);
  }
}

/** Format due-plan as_of (usually UTC ISO) for local display. */
export function formatDuePlanAsOf(asOf: string | null | undefined): string {
  if (!asOf) return "—";
  try {
    const d = new Date(asOf);
    if (Number.isNaN(d.getTime())) return asOf;
    return `${d.toLocaleString("zh-CN", { hour12: false })}（源 ${asOf}）`;
  } catch {
    return asOf;
  }
}
