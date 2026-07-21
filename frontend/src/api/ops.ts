/** Ops manual jobs — backend routers/ops_manual_run.py (no auth beyond same-origin proxy). */

import { apiGet, apiPost } from "./client";

export interface OpsJobStatus {
  job: string;
  label: string;
  running: boolean;
  writer_busy: boolean;
  owner: string | null;
  owner_pid: number | null;
  process_hint_running: boolean;
  alert_flags: Record<string, boolean>;
  log_path: string;
  log_tail: string[];
  log_mtime: number | null;
}

export interface OpsJobRunResp {
  job: string;
  accepted: boolean;
  pid: number;
}

export function fetchOpsJob(job: string): Promise<OpsJobStatus> {
  return apiGet<OpsJobStatus>(`/api/v3/ops/jobs/${encodeURIComponent(job)}`);
}

export function runOpsJob(job: string): Promise<OpsJobRunResp> {
  return apiPost<OpsJobRunResp>(`/api/v3/ops/jobs/${encodeURIComponent(job)}/run`);
}

/** True while flock or process hint says the chain is still alive. */
export function jobLooksActive(s: OpsJobStatus | null | undefined): boolean {
  if (!s) return false;
  return Boolean(s.writer_busy || s.process_hint_running || s.running);
}
