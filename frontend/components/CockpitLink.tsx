"use client";

import NextLink from "next/link";
import type { ComponentProps } from "react";
import { staticShell } from "@/lib/navigation";

export default function CockpitLink(props: ComponentProps<"a"> & { href: string }) {
  return staticShell ? <a {...props} /> : <NextLink {...props} />;
}
