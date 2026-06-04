import { Routes, Route, Navigate } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteDetailPage from "./pages/SuiteDetailPage";
import RunConsolePage from "./pages/RunConsolePage";
import RunOverviewPage from "./pages/RunOverviewPage";
import CaseResultPage from "./pages/CaseResultPage";
import CodeViewerPage from "./pages/CodeViewerPage";
import VocabularyPage from "./pages/VocabularyPage";

export default function App() {
  return (
    <div className="flex h-screen bg-surface-50">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto p-6">
          <Routes>
            <Route path="/" element={<Navigate to="/suites" replace />} />
            <Route path="/suites" element={<SuiteListPage />} />
            <Route path="/suites/:id" element={<SuiteDetailPage />} />
            <Route path="/suites/:id/run" element={<RunConsolePage />} />
            <Route path="/suites/:id/runs/:runId" element={<RunOverviewPage />} />
            <Route path="/suites/:id/runs/:runId/case/:caseId" element={<CaseResultPage />} />
            <Route path="/suites/:id/runs/:runId/case/:caseId/code" element={<CodeViewerPage />} />
            <Route path="/vocabulary" element={<VocabularyPage />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
