import { lazy } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";

// 路由级代码分割 (React.lazy): 每个页面独立 chunk, 首屏只载 Layout + 当前路由。
// 页面均为命名导出, 用 .then 适配成 lazy 需要的 { default }。Layout 非懒加载 (导航骨架首屏必需)。
const BriefingPage = lazy(() => import("./pages/BriefingPage").then((m) => ({ default: m.BriefingPage })));
const FacetExplorePage = lazy(() => import("./pages/FacetExplorePage").then((m) => ({ default: m.FacetExplorePage })));
const InstitutionDetailPage = lazy(() => import("./pages/InstitutionDetailPage").then((m) => ({ default: m.InstitutionDetailPage })));
const InstitutionsPage = lazy(() => import("./pages/InstitutionsPage").then((m) => ({ default: m.InstitutionsPage })));
const MarketPage = lazy(() => import("./pages/MarketPage").then((m) => ({ default: m.MarketPage })));
const PaperPage = lazy(() => import("./pages/PaperPage").then((m) => ({ default: m.PaperPage })));
const StockDossierPage = lazy(() => import("./pages/StockDossierPage").then((m) => ({ default: m.StockDossierPage })));
const WorkbenchPage = lazy(() => import("./pages/WorkbenchPage").then((m) => ({ default: m.WorkbenchPage })));

// HashRouter: 无需服务端 history fallback, vite preview / 任意静态托管直接可用
export function App() {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/market" replace />} />
          <Route path="/institutions" element={<InstitutionsPage />} />
          <Route path="/institutions/:holder" element={<InstitutionDetailPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/workbench" element={<WorkbenchPage />} />
          <Route path="/market" element={<MarketPage />} />
          <Route path="/briefing" element={<BriefingPage />} />
          <Route path="/explore" element={<FacetExplorePage />} />
          <Route path="/stock/:code" element={<StockDossierPage />} />
          <Route path="/stock" element={<Navigate to="/stock/600519" replace />} />
          <Route path="*" element={<Navigate to="/market" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
