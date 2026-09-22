"use client";

import { BarChart3, Bot, Calendar, File, Folder, Home, LogOut, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0] + parts[parts.length - 1]![0]).toUpperCase();
}

// Phase 27 nav restructure — see CLAUDE.md's Navigation section. "Upload" is
// deliberately not a nav item anymore: the route still exists and works
// exactly as before, just reached via the "Upload document" button on the
// Documents page now instead of its own sidebar entry.
const NAV_ITEMS = [
  { label: "Home", href: "/", icon: Home },
  { label: "Editing Workspace", href: "/workspace", icon: Bot },
  { label: "Documents", href: "/documents", icon: Folder },
  { label: "Tasks", href: "/tasks", icon: Calendar },
  { label: "Analytics", href: "/analytics", icon: BarChart3 },
  { label: "Settings", href: "/settings", icon: Settings },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col overflow-y-auto border-r border-line bg-sidebar-bg">
      <div className="flex items-center gap-2 px-5 py-6">
        <span className="flex h-7 w-7 items-center justify-center rounded-md text-ink">
          <File className="h-5 w-5" strokeWidth={1.75} />
        </span>
        <span className="font-serif text-[18px] font-medium text-ink">DocumentOS</span>
      </div>

      <nav className="flex flex-col gap-1 px-3">
        {NAV_ITEMS.map((item) => {
          // "/" would otherwise match every route via startsWith — only ever
          // active on an exact match; every other item still prefix-matches
          // (e.g. /documents/{id} keeps Documents highlighted).
          const active = item.href === "/" ? pathname === "/" : pathname?.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.label}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-ink text-paper"
                  : "text-ink-soft hover:bg-paper-raised hover:text-ink"
              )}
            >
              <Icon className="h-4 w-4" strokeWidth={1.75} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="flex-1" />

      <div className="flex items-center gap-1 border-t border-line px-3 py-4">
        <Link
          href="/settings"
          className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-2 py-1.5 transition-colors hover:bg-paper-raised"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-xs font-medium text-paper">
            {user ? getInitials(user.name) : "?"}
          </span>
          <div className="flex flex-1 flex-col overflow-hidden">
            <span className="truncate text-sm text-ink">{user?.name ?? "—"}</span>
            <span className="truncate text-xs text-ink-soft">{user?.email ?? ""}</span>
          </div>
        </Link>
        <button
          type="button"
          onClick={logout}
          aria-label="Log out"
          title="Log out"
          className="shrink-0 rounded-md p-1.5 text-ink-soft hover:bg-paper-raised hover:text-ink"
        >
          <LogOut className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </div>
    </aside>
  );
}
