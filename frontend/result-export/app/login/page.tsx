"use client";

import { LoginScreen } from "@/components/screens/LoginScreen";

/** The static companion has no client router: a successful sign-in loads the target page. */
export default function Page() {
  return <LoginScreen defaultPath="/workspace/apps" navigate={(path) => window.location.replace(path)} />;
}
