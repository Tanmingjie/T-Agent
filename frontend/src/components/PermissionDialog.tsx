import { useEffect, useState } from "react";
import { apiPost } from "../api/client";

interface Props {
  eventId: string;
  caseId: string;
  action: string;
  reason: string;
  suiteId: string;
  onResolved: () => void;
}

export default function PermissionDialog({ eventId, caseId, action, reason, suiteId, onResolved }: Props) {
  const [countdown, setCountdown] = useState(30);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    if (resolved) return;
    if (countdown <= 0) {
      // Auto-reject when countdown reaches zero
      respond("reject");
      return;
    }
    const t = setInterval(() => setCountdown((c) => c - 1), 1000);
    return () => clearInterval(t);
  }, [countdown, resolved]);

  async function respond(choice: "approve" | "reject") {
    setResolved(true);
    try {
      await apiPost(`/suites/${suiteId}/permission/${eventId}`, { choice });
    } catch (e) {
      console.error("Permission response failed:", e);
    }
    onResolved();
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 max-w-md w-full shadow-xl">
        <h3 className="text-lg font-bold text-red-600 mb-2">⚠ 高危操作 — 等待确认</h3>
        <p className="text-sm text-gray-600 mb-1">用例: {caseId}</p>
        <p className="text-sm text-gray-600 mb-1">操作: {action}</p>
        <p className="text-sm text-gray-600 mb-4">风险: {reason}</p>
        <p className="text-xs text-gray-400 mb-3">
          倒计时 {countdown}s 内未响应 → 自动拒绝
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => respond("approve")}
            disabled={resolved}
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 disabled:opacity-50"
          >
            ✓ 批准本次
          </button>
          <button
            onClick={() => respond("reject")}
            disabled={resolved}
            className="border border-red-500 text-red-600 px-4 py-2 rounded hover:bg-red-50 disabled:opacity-50"
          >
            ✕ 拒绝本次
          </button>
        </div>
      </div>
    </div>
  );
}