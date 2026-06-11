import { Routes, Route, Navigate } from "react-router-dom";
import RootLayout from "./components/RootLayout";
import SuiteLayout from "./components/SuiteLayout";
import ProjectOverviewPage from "./pages/ProjectOverviewPage";
import TasksPage from "./pages/TasksPage";
import SuiteCasesPage from "./pages/SuiteCasesPage";
import SuiteHistoryPage from "./pages/SuiteHistoryPage";
import SuiteReportsPage from "./pages/SuiteReportsPage";
import SuiteRunDetailPage from "./pages/SuiteRunDetailPage";
import SuiteSettingsPage from "./pages/SuiteSettingsPage";
import VocabularyPage from "./pages/VocabularyPage";
import ProjectSettingsPage from "./pages/ProjectSettingsPage";

export default function App() {
  return (
    <Routes>
      {/* 项目级:顶栏(只读项目) + 项目侧栏。测试任务页内含版本下拉(版本层降为选择器) */}
      <Route element={<RootLayout />}>
        <Route path="/" element={<ProjectOverviewPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/vocabulary" element={<VocabularyPage />} />
        <Route path="/settings" element={<ProjectSettingsPage />} />
      </Route>

      {/* 测试任务工作区(套件 id 全局唯一):用例 · 执行历史 · 报告 · 设置 */}
      <Route path="/suites/:id" element={<SuiteLayout />}>
        <Route index element={<SuiteCasesPage />} />
        <Route path="history" element={<SuiteHistoryPage />} />
        <Route path="reports" element={<SuiteReportsPage />} />
        <Route path="runs/:runId" element={<SuiteRunDetailPage />} />
        <Route path="settings" element={<SuiteSettingsPage />} />
      </Route>

      {/* 兜底:旧地址(/versions、/suites 等)或未知路径回落到概览,避免白屏 */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
