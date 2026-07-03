import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 后端端口真相源: backend/main.py 读 CM_PORT env, 默认 8000 (rg 实测 main.py:95)
const backendPort = process.env.CM_PORT || "8000";

export default defineConfig({
  // 生产 build 挂 FastAPI /app 路径 (main.py StaticFiles; 避开旧 dossier /assets 挂载撞路径)
  base: "/app/",
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: { echarts: ["echarts"] },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
});
