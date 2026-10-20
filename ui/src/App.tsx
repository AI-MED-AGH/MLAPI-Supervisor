import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { ApprovalsPage } from "./pages/Approvals";
import { KeysPage } from "./pages/Keys";
import { LoginPage } from "./pages/Login";
import { ModelDetailPage } from "./pages/ModelDetail";
import { ModelsPage } from "./pages/Models";
import { ObserversPage } from "./pages/Observers";
import { OverviewPage } from "./pages/Overview";

export function App() {
  const { status } = useAuth();
  if (status === "checking") return <Loading label="Connecting…" />;
  if (status === "out") return <LoginPage />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="models/:name" element={<ModelDetailPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="keys" element={<KeysPage />} />
        <Route path="observers" element={<ObserversPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
