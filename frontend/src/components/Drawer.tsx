import { useEffect } from "react";
import { X } from "lucide-react";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  width?: string;
  children: React.ReactNode;
}

export default function Drawer({
  open,
  onClose,
  title,
  width = "max-w-2xl",
  children,
}: DrawerProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      />
      {/* Panel — transform-gpu + will-change 让滑入只合成缓存图层、不每帧重绘整面内容(消滑入卡顿) */}
      <aside
        className={`fixed top-0 right-0 h-screen w-full ${width} bg-white border-l border-gray-200 shadow-xl z-50 flex flex-col transform-gpu will-change-transform transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="h-14 px-5 flex items-center justify-between border-b border-gray-200 shrink-0">
          <div className="min-w-0 flex-1">{title}</div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-gray-400 hover:text-surface-900 hover:bg-gray-100 transition-colors shrink-0"
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-hidden">{children}</div>
      </aside>
    </>
  );
}
