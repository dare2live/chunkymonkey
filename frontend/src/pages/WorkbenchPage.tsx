/** 工作台 — 手动 ops 触发面（ops_manual_run「前端按钮面板」）。
 *  当前只接线「数据更新」→ POST /api/v3/ops/jobs/daily_update/run + 状态轮询。
 *  多 tab / 资金流决策辅助 backlog 另开，不在本页堆产品面。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { fetchOpsJob, jobLooksActive, runOpsJob, type OpsJobStatus } from "../api/ops";
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
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await fetchOpsJob(DAILY_UPDATE_JOB);
      setStatus(s);
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
      setActionMsg(`已受理 pid=${resp.pid} — 下方日志会刷新；预检失败会写 ALERT flag`);
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
      {alerts.length > 0 && (
        <div className="banner-warn">
          告警 flag 仍在: {alerts.join(", ")} — 常见于预检硬停（如 PREFLIGHT BLOCK）；修好后成功跑会自清，或手工清 /tmp flag。
        </div>
      )}

      <Card
        title="数据更新"
        extra={<span className="mono">{statusLabel(status)}</span>}
      >
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
