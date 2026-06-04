import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";

export default function RootLayout() {
  return (
    <div className="flex h-screen bg-white text-surface-900">
      <Sidebar />
      <main className="flex-1 overflow-auto bg-gray-50/40">
        <div className="max-w-7xl mx-auto px-8 py-7">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
