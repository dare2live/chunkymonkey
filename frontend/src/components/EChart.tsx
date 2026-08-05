import { init, use } from "echarts/core";
import type { EChartsType, ECElementEvent, EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import {
  BarChart,
  HeatmapChart,
  LineChart,
  ParallelChart,
  SankeyChart,
  ScatterChart,
} from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  ParallelComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { useEffect, useRef } from "react";
import type { EChartsOption } from "echarts";

// 按需注册 (tree-shaking): 全代码库实测仅用到以下 series/组件 (line/bar/scatter/
// heatmap/sankey/parallel + tooltip/legend/grid/visualMap/title/parallel + mark*)。
// 新增图表类型时必须在此登记, 否则运行时 "series.type 未注册" 白屏。
use([
  CanvasRenderer,
  BarChart,
  HeatmapChart,
  LineChart,
  ParallelChart,
  SankeyChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  ParallelComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
]);

/** echarts 薄封装: 容器有尺寸后才 init (防 0 宽警告), ResizeObserver 自适应,
 *  option 变更 setOption(notMerge)。onClick (v3 下钻入口) 走 ref 取最新 handler,
 *  handler 变化不重建 chart。 */
export function EChart(props: {
  option: EChartsOption;
  height: number;
  onClick?: (params: ECElementEvent) => void;
}) {
  const divRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const optionRef = useRef(props.option);
  optionRef.current = props.option;
  const clickRef = useRef(props.onClick);
  clickRef.current = props.onClick;

  useEffect(() => {
    const el = divRef.current!;
    const ensure = () => {
      if (!chartRef.current) {
        if (el.clientWidth === 0 || el.clientHeight === 0) return;
        chartRef.current = init(el, undefined, { renderer: "canvas" });
        chartRef.current.setOption(optionRef.current as unknown as EChartsCoreOption, true);
        chartRef.current.on("click", (p) => clickRef.current?.(p as ECElementEvent));
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
    chartRef.current?.setOption(props.option as unknown as EChartsCoreOption, true);
  }, [props.option]);

  return <div ref={divRef} style={{ height: props.height, width: "100%" }} />;
}
