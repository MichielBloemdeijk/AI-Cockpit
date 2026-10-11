"use client";

import { useEffect, useState } from "react";

export default function LinkPhonePage() {
  const [deepLink, setDeepLink] = useState("");
  useEffect(() => {
    const fields = new URLSearchParams(window.location.hash.slice(1));
    const code = fields.get("code") || "";
    const server = fields.get("server") || "";
    // Accept links generated for this host only. Codes remain in the fragment,
    // outside HTTP requests, access logs and referrers.
    if (/^\d{6}$/.test(code) && server === window.location.origin) {
      setDeepLink(`jarvis://pair?${new URLSearchParams({ server, code })}`);
    }
  }, []);
  return <main className="mx-auto max-w-md space-y-5 p-8">
    <h1 className="text-2xl font-semibold">Link this phone to Jarvis</h1>
    <p>Keep Tailscale connected. Install the app if needed, then open your link. Jarvis links automatically and guides you through Android’s permission prompts.</p>
    <a className="block text-blue-400 underline" href="/api/jarvis/devices/android.apk">Install Jarvis for Android</a>
    {deepLink ? <a className="block rounded-lg bg-blue-600 p-3 text-center text-white" href={deepLink} rel="noreferrer">Open Jarvis and link phone</a> : <p>This link is incomplete. Create a fresh link from Jarvis Settings.</p>}
    <p className="text-sm">If the code has expired or was used, create another Link phone link. Installing updates keeps your existing link.</p>
  </main>;
}
