import { Routes, Route, Navigate } from "react-router-dom";
import RootLayout from "./components/RootLayout";
import VersionLayout from "./components/VersionLayout";
import SuiteLayout from "./components/SuiteLayout";
import ProjectOverviewPage from "./pages/ProjectOverviewPage";
import VersionListPage from "./pages/VersionListPage";
import VersionReportsPage from "./pages/VersionReportsPage";
import SuiteListPage from "./pages/SuiteListPage";
import SuiteCasesPage from "./pages/SuiteCasesPage";
import SuiteHistoryPage from "./pages/SuiteHistoryPage";
import SuiteRunDetailPage from "./pages/SuiteRunDetailPage";
import SuiteSettingsPage from "./pages/SuiteSettingsPage";
import VocabularyPage from "./pages/VocabularyPage";
import ProjectSettingsPage from "./pages/ProjectSettingsPage";

export default function App() {
  return (
    <Routes>
      {/* 项目级:顶栏(只读项目) + 项目侧栏 */}
      <Route element={<RootLayout />}>
        <Route path="/" element={<ProjectOverviewPage />} />
        <Route path="/versions" element={<VersionListPage />} />
        <Route path="/vocabulary" element={<VocabularyPage />} />
        <Route path="/settings" element={<ProjectSettingsPage />} />
      </Route>

      {/* 版本工作区:套件 · 报告(版本级,面包屑 项目›版本) */}
      <Route path="/versions/:vid" element={<VersionLayout />}>
        <Route index element={<SuiteListPage />} />
        <Route path="reports" element={<VersionReportsPage />} />
      </Route>

      {/* 套件工作区(套件 id 全局唯一,沿用现有路由) */}
      <Route path="/suites/:id" element={<SuiteLayout />}>
        <Route index element={<SuiteCasesPage />} />
        <Route path="history" element={<SuiteHistoryPage />} />
        <Route path="runs/:runId" element={<SuiteRunDetailPage />} />
        <Route path="settings" element={<SuiteSettingsPage />} />
      </Route>

      {/* 兜底:旧地址(/suites、/project-settings 等)或未知路径回落到概览,避免白屏 */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
