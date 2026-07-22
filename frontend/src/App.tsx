import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { BriefingPage } from "./pages/BriefingPage";
import { FacetExplorePage } from "./pages/FacetExplorePage";
import { InstitutionDetailPage } from "./pages/InstitutionDetailPage";
import { InstitutionsPage } from "./pages/InstitutionsPage";
import { MarketPage } from "./pages/MarketPage";
import { PaperPage } from "./pages/PaperPage";
import { StockDossierPage } from "./pages/StockDossierPage";
import { WorkbenchPage } from "./pages/WorkbenchPage";

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
