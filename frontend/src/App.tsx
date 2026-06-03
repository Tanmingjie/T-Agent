import { Routes, Route, Link } from "react-router-dom";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteDetailPage from "./pages/SuiteDetailPage";
import RunConsolePage from "./pages/RunConsolePage";
import RunOverviewPage from "./pages/RunOverviewPage";
import CaseResultPage from "./pages/CaseResultPage";
import CodeViewerPage from "./pages/CodeViewerPage";
import VocabularyPage from "./pages/VocabularyPage";

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="bg-slate-800 text-white px-6 py-3 flex items-center gap-4">
        <Link to="/suites" className="font-bold text-lg">T-agent</Link>
        <nav className="flex gap-4 text-sm">
          <Link to="/suites" className="hover:text-cyan-300">Suites</Link>
          <Link to="/vocabulary" className="hover:text-cyan-300">词汇表</Link>
        </nav>
      </header>
      <main className="max-w-7xl mx-auto p-6">
        <Routes>
          <Route path="/" element={<SuiteListPage />} />
          <Route path="/suites" element={<SuiteListPage />} />
          <Route path="/suites/:id" element={<SuiteDetailPage />} />
          <Route path="/suites/:id/run" element={<RunConsolePage />} />
          <Route path="/suites/:id/runs/:runId" element={<RunOverviewPage />} />
          <Route path="/suites/:id/runs/:runId/case/:caseId" element={<CaseResultPage />} />
          <Route path="/suites/:id/runs/:runId/case/:caseId/code" element={<CodeViewerPage />} />
          <Route path="/vocabulary" element={<VocabularyPage />} />
        </Routes>
      </main>
    </div>
  );
}