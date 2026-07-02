import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { InstitutionDetailPage } from "./pages/InstitutionDetailPage";
import { InstitutionsPage } from "./pages/InstitutionsPage";
import { PaperPage } from "./pages/PaperPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

// HashRouter: 无需服务端 history fallback, vite preview / 任意静态托管直接可用
export function App() {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/institutions" replace />} />
          <Route path="/institutions" element={<InstitutionsPage />} />
          <Route path="/institutions/:holder" element={<InstitutionDetailPage />} />
          <Route path="/paper" element={<PaperPage />} />
          <Route path="/workbench" element={<PlaceholderPage title="工作台" />} />
          <Route path="/market" element={<PlaceholderPage title="市场感知" />} />
          <Route path="*" element={<Navigate to="/institutions" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
