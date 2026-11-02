"use client";

import { useRouter } from "next/navigation";
import { LoginScreen } from "@/components/screens/LoginScreen";

export default function LoginPage() {
  const router = useRouter();
  return <LoginScreen defaultPath="/chat" navigate={(path) => router.replace(path)} />;
}
