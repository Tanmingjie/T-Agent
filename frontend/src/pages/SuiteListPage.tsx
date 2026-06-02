import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPost, apiDelete } from "../api/client";

interface Suite {
  id: string;
  name: string;
  base_url: string;
}

export default function SuiteListPage() {
  const [suites, setSuites] = useState<Suite[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const navigate = useNavigate();

  async function load() {
    setSuites(await apiGet<Suite[]>("/suites"));
  }
  useEffect(() => { load(); }, []);

  async function create() {
    await apiPost("/suites", { name, base_url: baseUrl });
    setShowCreate(false);
    setName("");
    setBaseUrl("");
    load();
  }

  async function remove(id: string) {
    await apiDelete(`/suites/${id}`);
    load();
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Suites</h2>
        <button
          onClick={() => setShowCreate(true)}
          className="bg-slate-800 text-white px-4 py-2 rounded hover:bg-slate-700"
        >
          + 新建
        </button>
      </div>

      {showCreate && (
        <div className="mb-6 p-4 border rounded bg-white">
          <input
            className="border px-3 py-2 rounded w-full mb-2"
            placeholder="Suite 名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="border px-3 py-2 rounded w-full mb-2"
            placeholder="Base URL (e.g. https://example.com)"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              onClick={create}
              className="bg-cyan-600 text-white px-4 py-1 rounded"
            >
              创建
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="border px-4 py-1 rounded"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {suites.length === 0 && (
        <p className="text-gray-500 text-center py-20">
          还没有 Suite。创建你的第一个测试 Suite。
        </p>
      )}

      <div className="grid gap-4">
        {suites.map((s) => (
          <div
            key={s.id}
            className="bg-white border rounded p-4 flex items-center justify-between cursor-pointer hover:shadow"
            onClick={() => navigate(`/suites/${s.id}`)}
          >
            <div>
              <h3 className="font-semibold">{s.name}</h3>
              <p className="text-sm text-gray-500">{s.base_url}</p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); remove(s.id); }}
              className="text-red-500 text-sm hover:underline"
            >
              删除
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
