import type { Metadata, Viewport } from "next";
import "../../app/globals.css";

export const metadata: Metadata = { title: "Cockpit result", robots: { index: false, follow: false } };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body className="bg-zinc-950 text-zinc-100 antialiased">{children}</body></html>;
}
