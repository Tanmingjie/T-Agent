import { Routes, Route, Navigate } from "react-router-dom";
import RootLayout from "./components/RootLayout";
import SuiteLayout from "./components/SuiteLayout";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteCasesPage from "./pages/SuiteCasesPage";
import SuiteHistoryPage from "./pages/SuiteHistoryPage";
import SuiteSettingsPage from "./pages/SuiteSettingsPage";
import RunOverviewPage from "./pages/RunOverviewPage";
import CaseResultPage from "./pages/CaseResultPage";
import CodeViewerPage from "./pages/CodeViewerPage";
import VocabularyPage from "./pages/VocabularyPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/suites" replace />} />

      {/* Global-sidebar layout */}
      <Route element={<RootLayout />}>
        <Route path="/suites" element={<SuiteListPage />} />
        <Route path="/vocabulary" element={<VocabularyPage />} />
      </Route>

      {/* Suite workspace layout (suite-scoped nav + breadcrumb) */}
      <Route path="/suites/:id" element={<SuiteLayout />}>
        <Route index element={<SuiteCasesPage />} />
        <Route path="history" element={<SuiteHistoryPage />} />
        <Route path="settings" element={<SuiteSettingsPage />} />
      </Route>

      {/* Historical run detail flow */}
      <Route element={<RootLayout />}>
        <Route path="/suites/:id/runs/:runId" element={<RunOverviewPage />} />
        <Route
          path="/suites/:id/runs/:runId/case/:caseId"
          element={<CaseResultPage />}
        />
        <Route
          path="/suites/:id/runs/:runId/case/:caseId/code"
          element={<CodeViewerPage />}
        />
      </Route>
    </Routes>
  );
}
