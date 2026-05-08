"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MessageSquare, ListTodo, BookOpen, Settings, LogOut, Cpu, Blocks } from "lucide-react";
import clsx from "clsx";
import { logout } from "@/lib/api";
import { useRouter } from "next/navigation";

const NAV = [
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/background", label: "Background", icon: ListTodo },
  { href: "/workspace/apps", label: "Apps", icon: Blocks },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  const handleLogout = async () => {
    await logout();
    router.push("/login");
  };

  return (
    <>
      <aside className="hidden h-full w-56 flex-col border-r border-zinc-700 bg-zinc-900 md:flex">
        <div className="flex items-center gap-2 border-b border-zinc-700 px-4 py-5">
          <Cpu size={20} className="text-blue-400 flex-shrink-0" />
          <span className="text-sm font-semibold text-zinc-100">AI Cockpit</span>
        </div>

        <nav className="flex-1 space-y-1 px-2 py-4">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                  active
                    ? "bg-blue-600 text-white"
                    : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                )}
              >
                <Icon size={18} className="flex-shrink-0" />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="px-2 pb-4">
          <button
            onClick={handleLogout}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
          >
            <LogOut size={18} className="flex-shrink-0" />
            <span>Logout</span>
          </button>
        </div>
      </aside>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-zinc-800 bg-zinc-950/95 px-2 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pt-2 backdrop-blur md:hidden">
        <div className="mx-auto flex max-w-md items-center gap-1 rounded-3xl border border-zinc-800 bg-zinc-900/90 p-1.5 shadow-[0_-12px_40px_rgba(0,0,0,0.35)]">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  "flex min-w-0 flex-1 flex-col items-center gap-1 rounded-2xl px-2 py-2 text-[11px] transition-colors",
                  active
                    ? "bg-blue-600 text-white"
                    : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                )}
              >
                <Icon size={18} className="flex-shrink-0" />
                <span className="truncate">{label}</span>
              </Link>
            );
          })}
          <button
            onClick={handleLogout}
            className="flex flex-1 min-w-0 flex-col items-center gap-1 rounded-2xl px-2 py-2 text-[11px] text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
          >
            <LogOut size={18} className="flex-shrink-0" />
            <span className="truncate">Logout</span>
          </button>
        </div>
      </nav>
    </>
  );
}
