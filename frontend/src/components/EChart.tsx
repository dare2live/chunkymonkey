import * as echarts from "echarts";
import { useEffect, useRef } from "react";

/** echarts 薄封装: 容器有尺寸后才 init (防 0 宽警告), ResizeObserver 自适应,
 *  option 变更 setOption(notMerge)。 */
export function EChart(props: { option: echarts.EChartsOption; height: number }) {
  const divRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const optionRef = useRef(props.option);
  optionRef.current = props.option;

  useEffect(() => {
    const el = divRef.current!;
    const ensure = () => {
      if (!chartRef.current) {
        if (el.clientWidth === 0 || el.clientHeight === 0) return;
        chartRef.current = echarts.init(el, undefined, { renderer: "canvas" });
        chartRef.current.setOption(optionRef.current, true);
      } else {
        chartRef.current.resize();
      }
    };
    const ro = new ResizeObserver(ensure);
    ro.observe(el);
    ensure();
    return () => {
      ro.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(props.option, true);
  }, [props.option]);

  return <div ref={divRef} style={{ height: props.height, width: "100%" }} />;
}
