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
  disabled_reason: string | null;
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

/** True while flock or process hint says the chain is still alive. */
export function jobLooksActive(s: OpsJobStatus | null | undefined): boolean {
  if (!s) return false;
  return Boolean(s.writer_busy || s.process_hint_running || s.running);
}

/** Per-node activity: prefer this job's process hint (writer_busy is global flock). */
export function nodeLooksActive(s: OpsJobStatus | null | undefined): boolean {
  if (!s) return false;
  return Boolean(s.process_hint_running);
}

export function nodeCardTone(
  node: PipelineNode,
): "idle" | "running" | "ok" | "alert" | "disabled" {
  if (!node.runnable) return "disabled";
  const s = node.status;
  if (!s) return "idle";
  if (nodeLooksActive(s)) return "running";
  const act = deriveActivityFallback({
    ...s,
    // Avoid painting every card "running" from global writer flock.
    writer_busy: false,
    running: false,
  });
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
    if (tail[i].includes("=== ChunkyMonkey daily update") || tail[i].includes("=== ChunkyMonkey pipeline stage")) {
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
    ["fail", "失败 / 阻断", /PREFLIGHT BLOCK|FAIL rc=|DEGRADED:/],
  ];
  for (const line of [...run].reverse()) {
    for (const [id, label, re] of checks) {
      if (active && id === "fail") continue;
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
  if (active) {
    summary = `正在: ${phaseLabel}`;
    if (s.owner) summary += ` · writer=${s.owner}`;
    if (s.owner_pid != null) summary += ` pid=${s.owner_pid}`;
    if (stale) summary += ` · 日志已 ${Math.floor(age || 0)}s 无新行（进程仍在）`;
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
    blocking_reason: s.alert_summary ?? null,
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
