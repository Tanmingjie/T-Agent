import { Routes, Route, Navigate } from "react-router-dom";
import RootLayout from "./components/RootLayout";
import SuiteLayout from "./components/SuiteLayout";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteCasesPage from "./pages/SuiteCasesPage";
import SuiteHistoryPage from "./pages/SuiteHistoryPage";
import SuiteRunDetailPage from "./pages/SuiteRunDetailPage";
import SuiteSettingsPage from "./pages/SuiteSettingsPage";
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
        <Route path="runs/:runId" element={<SuiteRunDetailPage />} />
        <Route path="settings" element={<SuiteSettingsPage />} />
      </Route>
    </Routes>
  );
}
