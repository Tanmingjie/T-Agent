// 版本列表(项目级页面)。版本由内网系统维护,本平台只读展示;点击进入版本工作区。
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, ChevronRight } from "lucide-react";
import { apiGet } from "../api/client";
import { getProjectId } from "../lib/session";

interface Version {
  id: string;
  name: string;
}

export default function VersionListPage() {
  const pid = getProjectId();
  const navigate = useNavigate();
  const [versions, setVersions] = useState<Version[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pid) {
      setError("未指定项目。请通过内网系统进入,或在 URL 加 ?project=<id>。");
      return;
    }
    apiGet<Version[]>(`/projects/${pid}/versions`)
      .then(setVersions)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [pid]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">版本</h1>
        <p className="text-sm text-gray-500 mt-1">
          选择一个版本进入其测试任务与报告。版本由内网系统维护。
        </p>
      </div>

      {error && (
        <div className="mb-5 p-3 bg-amber-50 border border-amber-200 rounded-md text-amber-700 text-sm">
          {error}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        {versions.length === 0 && !error && (
          <div className="px-5 py-16 text-center">
            <GitBranch size={36} className="mx-auto text-gray-300 mb-3" />
            <p className="text-gray-500 text-sm mb-1">该项目暂无版本</p>
            <p className="text-xs text-gray-400">版本在内网系统中创建后会出现在这里</p>
          </div>
        )}
        {versions.map((v) => (
          <button
            key={v.id}
            onClick={() => navigate(`/versions/${v.id}`)}
            className="w-full flex items-center justify-between px-5 py-4 border-b border-gray-100 last:border-0 hover:bg-gray-50/70 transition-colors group text-left"
          >
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-md bg-brand-50 flex items-center justify-center shrink-0">
                <GitBranch size={16} className="text-brand-600" />
              </div>
              <span className="font-medium text-surface-900 group-hover:text-brand-700 transition-colors">
                {v.name}
              </span>
            </div>
            <ChevronRight size={18} className="text-gray-300 group-hover:text-brand-600" />
          </button>
        ))}
      </div>
    </div>
  );
}
