import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { FolderGit2 } from "lucide-react";
import Sidebar from "./Sidebar";
import { apiGet } from "../api/client";
import { getProjectId } from "../lib/session";

// 顶栏:只读展示当前项目(内网跳转锁定)。本平台不切/不建项目。
function ProjectTopBar() {
  const pid = getProjectId();
  const [name, setName] = useState("");

  useEffect(() => {
    if (!pid) return;
    apiGet<{ name: string }>(`/projects/${pid}`)
      .then((p) => setName(p.name))
      .catch(() => setName(""));
  }, [pid]);

  return (
    <div className="h-14 border-b border-gray-200 bg-white px-8 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-2 text-sm">
        <FolderGit2 size={16} className="text-gray-400" />
        {pid ? (
          <span className="font-medium text-surface-900">{name || pid}</span>
        ) : (
          <span className="text-gray-400">
            未指定项目 · 单机模式(URL 加 ?project=&lt;id&gt; 进入项目)
          </span>
        )}
      </div>
      <span
        className={`px-2 py-0.5 rounded text-xs font-medium ${
          pid ? "bg-brand-50 text-brand-700" : "bg-gray-100 text-gray-500"
        }`}
        title="集成内网系统后由 IDaaS 提供身份与项目"
      >
        {pid ? "平台" : "单机"}
      </span>
    </div>
  );
}

export default function RootLayout() {
  return (
    <div className="flex h-screen bg-white text-surface-900">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <ProjectTopBar />
        <main className="flex-1 overflow-auto bg-canvas">
          <div className="px-8 py-7">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
