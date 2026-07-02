/** echarts / canvas 配色常量 — 与 styles.css :root 同值同步 (echarts 不认 CSS 变量)。
 *  改主题 token 时: styles.css :root 与本文件必须两处同改。 */
export const UI = {
  bg: "#f7f7f5", // --bg 暖纸白
  bgPanel: "#ffffff", // --bg-panel 卡片纯白
  bgPanel2: "#f2f1ee", // --bg-panel-2 次级面板/表头/hover
  border: "#e4e2dd", // --border
  borderSoft: "#eeede9", // --border-soft
  text: "#1c1c1a", // --text
  textDim: "#6f6e69", // --text-dim
  textFaint: "#767571", // --text-faint
  accent: "#3b66d4", // --accent 克制蓝 (链接/选中态)
  up: "#d4342c", // --up 涨红 (A股红涨)
  down: "#0f8a4e", // --down 跌绿
  warn: "#a06a00", // --warn
} as const;

/** 半透明变体 (面积图填充等), 基于 UI.up/down 派生。 */
export const UI_ALPHA = {
  upArea: "rgba(212, 52, 44, 0.14)", // UI.up @ 14% — 涨停家数面积填充
} as const;
