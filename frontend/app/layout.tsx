import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PodcastFactory",
  description: "Turn assigned readings into a two-host podcast for Executive MBA students.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0b0d12] text-slate-100 antialiased">{children}</body>
    </html>
  );
}
