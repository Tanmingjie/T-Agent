// 平台化 T-P11:侧栏底部「用户 + 项目」切换条。
// 设置用户名(X-User)、选/建项目;切换项目后整页刷新,让各页按新作用域重取数据
// (本应用为按页取数、无全局 store,刷新最稳)。单机模式下不设用户也能用。
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Settings, FolderGit2, UserRound } from "lucide-react";
import { apiGet, apiPost } from "../api/client";
import { getUser, setUser, getProjectId, setProjectId } from "../lib/session";

interface Project {
  id: string;
  name: string;
}

export default function ProjectBar() {
  const [user, setUserState] = useState(getUser());
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState(getProjectId());
  const [editingUser, setEditingUser] = useState(false);

  async function loadProjects() {
    try {
      const list = await apiGet<Project[]>("/projects");
      setProjects(list);
    } catch {
      setProjects([]); // 单机模式或未认证:列表空,不阻塞使用
    }
  }

  useEffect(() => {
    loadProjects();
  }, [user]);

  function commitUser() {
    setUser(user.trim());
    setUserState(user.trim());
    setEditingUser(false);
  }

  function switchProject(next: string) {
    setProjectId(next);
    setPid(next);
    window.location.reload(); // 按新项目作用域重取各页数据
  }

  async function createProject() {
    const name = window.prompt("新建项目名称");
    if (!name) return;
    const p = await apiPost<{ id: string }>("/projects", { name });
    await loadProjects();
    switchProject(p.id);
  }

  return (
    <div className="px-4 py-3 border-t border-gray-200 space-y-2 text-xs">
      {/* 用户 */}
      <div className="flex items-center gap-2 text-gray-600">
        <UserRound size={14} />
        {editingUser ? (
          <input
            autoFocus
            value={user}
            onChange={(e) => setUserState(e.target.value)}
            onBlur={commitUser}
            onKeyDown={(e) => e.key === "Enter" && commitUser()}
            placeholder="用户名"
            className="flex-1 min-w-0 border border-gray-300 rounded px-1.5 py-0.5 text-xs"
          />
        ) : (
          <button
            className="flex-1 text-left truncate hover:text-surface-900"
            onClick={() => setEditingUser(true)}
            title="点击设置用户名(X-User)"
          >
            {user || <span className="text-gray-400">未登录(单机模式)</span>}
          </button>
        )}
      </div>

      {/* 项目选择 */}
      <div className="flex items-center gap-2 text-gray-600">
        <FolderGit2 size={14} />
        <select
          value={pid}
          onChange={(e) => switchProject(e.target.value)}
          className="flex-1 min-w-0 border border-gray-300 rounded px-1.5 py-1 text-xs bg-white"
        >
          <option value="">全部 / 单机</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center justify-between">
        <button
          onClick={createProject}
          className="text-brand-700 hover:text-brand-800"
        >
          + 新建项目
        </button>
        {pid && (
          <NavLink
            to="/project-settings"
            className="flex items-center gap-1 text-gray-500 hover:text-surface-900"
          >
            <Settings size={13} /> 设置
          </NavLink>
        )}
      </div>
    </div>
  );
}
