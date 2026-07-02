import { useCallback, useEffect, useRef, useState } from "react";

/** 极简事件总线 — 动作 (入池/平仓/mark) 后广播 topic, 订阅该 topic 的卡片各自重取。 */
const bus = new EventTarget();

export function emitTopic(topic: string): void {
  bus.dispatchEvent(new Event(topic));
}

export interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * widget 独立取数原则: 每卡片一个 useFetch, 独立 loading/error, 一个挂了不拖垮页面。
 * deps 变化 / topic 广播 / 手动 reload 都会重取。
 */
export function useFetch<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  topics: string[] = [],
): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const handler = () => setTick((t) => t + 1);
    topics.forEach((t) => bus.addEventListener(t, handler));
    return () => topics.forEach((t) => bus.removeEventListener(t, handler));
    // topics 约定为字面量数组, 用 join 做稳定依赖
  }, [topics.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcherRef
      .current()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps

  return { data, loading, error, reload };
}
