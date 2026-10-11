"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Cpu, Lock } from "lucide-react";
import { checkAuth, getAccessConfig, login } from "@/lib/api";
import { defaultLoginPath, safeNextPath, staticShell } from "@/lib/navigation";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [passwordRequired, setPasswordRequired] = useState<boolean | null>(null);
  const router = useRouter();
  const [nextPath, setNextPath] = useState(defaultLoginPath);

  useEffect(() => {
    const target = safeNextPath(new URLSearchParams(window.location.search).get("next"));
    setNextPath(target);
    let active = true;
    async function prepare() {
      try {
        if (!(await checkAuth())) {
          if (navigator.userAgent.includes("JarvisPhone/")) {
            throw new Error("Reconnect or link this phone in the Jarvis Android app.");
          }
          const config = await getAccessConfig();
          if (!active) return;
          setPasswordRequired(config.password_required);
          if (config.password_required) return;
          await login("");
        }
        if (active) window.location.replace(target);
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "Unable to connect.");
      }
    }
    void prepare();
    return () => { active = false; };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const form = e.currentTarget as HTMLFormElement;
    const submittedPassword = String(new FormData(form).get("password") ?? "");

    if (!submittedPassword) {
      setError("Enter a password.");
      return;
    }

    setError("");
    setLoading(true);
    try {
      await login(submittedPassword);
      if (staticShell) window.location.replace(nextPath);
      else router.replace(nextPath);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950 px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-blue-600 flex items-center justify-center mb-4">
            <Cpu size={28} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold text-zinc-100">AI Cockpit</h1>
          <p className="text-sm text-zinc-400 mt-1">Personal AI command center</p>
        </div>

        {/* Form */}
        {passwordRequired ? <form onSubmit={handleSubmit} className="space-y-4">
          <div className="relative">
            <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              name="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoFocus
              className="w-full bg-zinc-800 border border-zinc-700 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>

          {error && (
            <p className="text-sm text-red-400 text-center">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-3 rounded-xl text-sm transition-colors"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form> : <div className="text-center text-zinc-300"><p>{error || "Connecting to Jarvis…"}</p>{error && <button className="mt-4 underline" onClick={() => window.location.reload()}>Try again</button>}</div>}
      </div>
    </div>
  );
}
