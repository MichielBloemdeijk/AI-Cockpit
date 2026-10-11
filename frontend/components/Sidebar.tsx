"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import { AudioLines, MessageSquare, ListTodo, BookOpen, Settings, LogOut, Cpu, Blocks, MoreHorizontal, X } from "lucide-react";
import clsx from "clsx";
import { logout } from "@/lib/api";
import { useRouter } from "next/navigation";

const NAV = [
  { href: "/jarvis", label: "Jarvis", icon: AudioLines },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/tasks", label: "Tasks", icon: ListTodo },
  { href: "/workspace/apps", label: "Apps", icon: Blocks },
  { href: "/wiki", label: "Wiki", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings },
];

const MOBILE_NAV = NAV.slice(0, 3);

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [moreOpen, setMoreOpen] = useState(false);

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

      {moreOpen && (
        <div className="fixed inset-0 z-40 bg-black/55 md:hidden" onClick={() => setMoreOpen(false)}>
          <section
            id="mobile-more-navigation"
            role="dialog"
            aria-label="More navigation"
            className="mobile-safe-more-menu absolute inset-x-2 rounded-3xl border border-zinc-700 bg-zinc-900 p-3 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-2 flex min-h-12 items-center justify-between px-2">
              <span className="text-sm font-semibold text-zinc-100">More</span>
              <button type="button" aria-label="Close More" onClick={() => setMoreOpen(false)} className="flex h-12 w-12 items-center justify-center rounded-2xl text-zinc-400 hover:bg-zinc-800">
                <X size={20} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {NAV.slice(3).map(({ href, label, icon: Icon }) => (
                <Link key={href} href={href} onClick={() => setMoreOpen(false)} className="flex min-h-12 items-center gap-3 rounded-2xl border border-zinc-800 bg-zinc-950 px-4 text-sm text-zinc-200">
                  <Icon size={19} className="text-zinc-400" /> {label}
                </Link>
              ))}
              <button onClick={handleLogout} className="flex min-h-12 items-center gap-3 rounded-2xl border border-zinc-800 bg-zinc-950 px-4 text-sm text-zinc-200">
                <LogOut size={19} className="text-zinc-400" /> Logout
              </button>
            </div>
          </section>
        </div>
      )}

      <nav aria-label="Primary mobile navigation" className="mobile-safe-nav fixed inset-x-0 bottom-0 z-50 border-t border-zinc-800 bg-zinc-950/95 px-2 pt-2 backdrop-blur md:hidden">
        <div className="mx-auto flex max-w-md items-center gap-1 rounded-3xl border border-zinc-800 bg-zinc-900/90 p-1.5 shadow-[0_-12px_40px_rgba(0,0,0,0.35)]">
          {MOBILE_NAV.map(({ href, label, icon: Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={clsx(
                  "flex min-h-12 min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-2xl px-2 text-[11px] transition-colors",
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
            type="button"
            aria-expanded={moreOpen}
            aria-controls="mobile-more-navigation"
            onClick={() => setMoreOpen((value) => !value)}
            className={clsx(
              "flex min-h-12 min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-2xl px-2 text-[11px] transition-colors",
              moreOpen || NAV.slice(3).some((item) => pathname.startsWith(item.href))
                ? "bg-blue-600 text-white"
                : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100",
            )}
          >
            <MoreHorizontal size={18} className="flex-shrink-0" />
            <span className="truncate">More</span>
          </button>
        </div>
      </nav>
    </>
  );
}
