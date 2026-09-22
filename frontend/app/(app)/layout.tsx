"use client";

import { AuthGuard } from "@/components/auth-guard";
import { CopilotPanel } from "@/components/copilot-panel";
import { Sidebar } from "@/components/sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div className="flex">
        <Sidebar />
        <main className="min-h-screen min-w-0 flex-1">{children}</main>
        <CopilotPanel />
      </div>
    </AuthGuard>
  );
}
