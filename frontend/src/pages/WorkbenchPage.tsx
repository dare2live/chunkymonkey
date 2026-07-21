/** 工作台 — 手动 ops 触发面（ops_manual_run「前端按钮面板」）。
 *  当前只接线「数据更新」→ POST /api/v3/ops/jobs/daily_update/run + 状态轮询。
 *  多 tab / 资金流决策辅助 / 模块化 step cards (Capability E) backlog 另开，不在本页堆产品面。
 *  本页只保证 one-click 运行时可观测：当前阶段 / 最近进度行 / 日志时间 / 告警原因。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
  deriveActivityFallback,
  fetchOpsJob,
  formatLogMtime,
  jobLooksActive,
  runOpsJob,
  type OpsJobStatus,
} from "../api/ops";
import { Card } from "../components/Card";

const DAILY_UPDATE_JOB = "daily_update";
const POLL_MS = 2500;

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

export function WorkbenchPage() {
  const [status, setStatus] = useState<OpsJobStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [polledAt, setPolledAt] = useState<number | null>(null);
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchOpsJob(DAILY_UPDATE_JOB);
      setStatus(s);
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

  useEffect(() => {
    const active = jobLooksActive(status);
    if (!active) {
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
  }, [status, refresh]);

  const onRun = async () => {
    setTriggering(true);
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
    }
  };

  const busy = triggering || jobLooksActive(status);
  const alerts = activeAlertNames(status?.alert_flags);
  const tail = status?.log_tail ?? [];
  const activity = deriveActivityFallback(status);
  const blocking =
    status?.alert_summary ||
    activity?.blocking_reason ||
    (alerts.length ? `flag: ${alerts.join(", ")}` : null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>工作台</h1>
          <p className="state-hint" style={{ textAlign: "left", padding: "4px 0 0" }}>
            手动触发数据底座链（preflight → 获取 → 清洗/派生 → 加工 → 存储）。不自动调度。
          </p>
        </div>
        <div className="mark-ctl">
          {actionMsg && <span className="mark-msg">{actionMsg}</span>}
          <button className="btn btn-primary" onClick={() => void onRun()} disabled={busy}>
            {triggering ? "提交中…" : jobLooksActive(status) ? "更新进行中…" : "数据更新"}
          </button>
        </div>
      </div>

      {loadError && (
        <div className="banner-warn">状态加载失败: {loadError}</div>
      )}
      {actionError && (
        <div className="banner-warn">触发失败: {actionError}</div>
      )}
      {blocking && !jobLooksActive(status) && (
        <div className="banner-warn">
          阻断 / 告警: {blocking}
          {alerts.length > 0
            ? ` （${alerts.join(", ")} — 修好后成功跑会自清，或手工清 /tmp flag）`
            : null}
        </div>
      )}
      {blocking && jobLooksActive(status) && (
        <div className="banner-info">
          注意: 仍有历史 ALERT flag（{blocking}）。当前链已在跑；成功结束通常会自清。
        </div>
      )}

      <Card
        title="数据更新"
        extra={<span className="mono">{statusLabel(status)}</span>}
      >
        <div
          className={
            jobLooksActive(status)
              ? "ops-activity ops-activity-live"
              : blocking
                ? "ops-activity ops-activity-alert"
                : "ops-activity"
          }
        >
          <div className="ops-activity-label">当前活动</div>
          <div className="ops-activity-summary">{activity?.summary ?? "—"}</div>
          {activity?.progress_line && (
            <div className="ops-activity-progress mono">{activity.progress_line}</div>
          )}
          <div className="ops-activity-meta mono">
            阶段={activity?.phase_label ?? "—"}
            {" · "}
            日志更新={formatLogMtime(status?.log_mtime ?? null)}
            {activity?.log_age_s != null ? `（${Math.floor(activity.log_age_s)}s 前）` : ""}
            {" · "}
            轮询={polledAt ? new Date(polledAt).toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
            {status?.owner ? ` · writer=${status.owner}` : ""}
            {status?.owner_pid != null ? ` pid=${status.owner_pid}` : ""}
          </div>
        </div>

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

        <div className="ops-log-head">
          <span>最近日志尾</span>
          <button className="btn" onClick={() => void refresh()} disabled={triggering}>
            刷新状态
          </button>
        </div>
        {tail.length === 0 ? (
          <div className="state-hint">尚无日志（未跑过或日志文件不存在）</div>
        ) : (
          <pre className="ops-log-tail mono">{tail.join("\n")}</pre>
        )}
      </Card>
    </div>
  );
}
