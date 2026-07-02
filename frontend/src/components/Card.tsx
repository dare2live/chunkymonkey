import type { ReactNode } from "react";
import type { FetchState } from "../hooks/useFetch";

/** widget 容器: 独立 loading / 失败态 / 空态, 一个卡片挂了不拖垮页面。 */
export function Card(props: { title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>{props.title}</h2>
        {props.extra && <div className="card-extra">{props.extra}</div>}
      </header>
      <div className="card-body">{props.children}</div>
    </section>
  );
}

/** 按取数状态渲染: loading → spinner, error → 失败态+重试, 空 → 空态, 有数 → children(data)。 */
export function FetchGate<T>(props: {
  state: FetchState<T>;
  empty?: (data: T) => boolean;
  emptyHint?: string;
  children: (data: T) => ReactNode;
}) {
  const { state } = props;
  if (state.loading && state.data === null) {
    return <div className="state-hint">加载中…</div>;
  }
  if (state.error) {
    return (
      <div className="state-error">
        <div>加载失败: {state.error}</div>
        <button className="btn" onClick={state.reload}>
          重试
        </button>
      </div>
    );
  }
  if (state.data === null) {
    return <div className="state-hint">无数据</div>;
  }
  if (props.empty && props.empty(state.data)) {
    return <div className="state-hint">{props.emptyHint ?? "暂无数据"}</div>;
  }
  return <>{props.children(state.data)}</>;
}
