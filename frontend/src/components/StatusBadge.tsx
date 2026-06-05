import { CheckCircle, XCircle, Loader2, Clock, AlertTriangle, Wrench } from "lucide-react";

type Status = "passed" | "failed" | "running" | "pending" | "healing" | "completed" | "aborted";

const config: Record<Status, { icon: React.ReactNode; cls: string }> = {
  passed:    { icon: <CheckCircle size={14} />, cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  failed:    { icon: <XCircle size={14} />,     cls: "bg-red-50 text-red-700 border-red-200" },
  running:   { icon: <Loader2 size={14} className="animate-spin" />, cls: "bg-blue-50 text-blue-700 border-blue-200" },
  pending:   { icon: <Clock size={14} />,       cls: "bg-gray-50 text-gray-500 border-gray-200" },
  healing:   { icon: <Wrench size={14} />,      cls: "bg-amber-50 text-amber-700 border-amber-200" },
  completed: { icon: <CheckCircle size={14} />, cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  aborted:   { icon: <AlertTriangle size={14} />, cls: "bg-red-50 text-red-700 border-red-200" },
};

export default function StatusBadge({ status }: { status: Status }) {
  const s = config[status] ?? config.pending;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${s.cls}`}>
      {s.icon}
      <span>{status}</span>
    </span>
  );
}
