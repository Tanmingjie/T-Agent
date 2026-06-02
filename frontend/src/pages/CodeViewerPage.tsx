import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { apiGet } from "../api/client";
import FileTree from "../components/FileTree";

export default function CodeViewerPage() {
  const { id, runId, caseId } = useParams<{ id: string; runId: string; caseId: string }>();
  const navigate = useNavigate();
  const [files, setFiles] = useState<Record<string, string>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ files: Record<string, string> }>(`/suites/${id}/runs/${runId}/cases/${caseId}/code`)
      .then((data) => {
        setFiles(data.files);
        const first = Object.keys(data.files)[0];
        if (first) setSelectedFile(first);
      });
  }, [id, runId, caseId]);

  const currentContent = selectedFile ? files[selectedFile] : "";
  const lang = selectedFile?.endsWith(".feature") ? "gherkin" : "python";

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${caseId}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回结果
      </button>

      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">{caseId} — 代码</h2>
        <a
          href={`/api/suites/${id}/runs/${runId}/cases/${caseId}/code/download`}
          className="bg-cyan-600 text-white px-4 py-1 rounded text-sm hover:bg-cyan-700"
        >
          下载 .zip
        </a>
      </div>

      <div className="grid grid-cols-[200px_1fr] gap-4" style={{ height: "70vh" }}>
        <div className="bg-white border rounded p-4">
          <FileTree files={files} onSelect={setSelectedFile} selected={selectedFile} />
        </div>
        <div className="bg-white border rounded overflow-hidden">
          <Editor
            height="100%"
            language={lang}
            value={currentContent}
            theme="vs-dark"
            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
          />
        </div>
      </div>
    </div>
  );
}
