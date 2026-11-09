import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import { KeyNames } from "@/components/keys";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "FinePoint Perception", template: "%s · FinePoint Perception" },
  description: "Recordings, transcripts and the one-clock multi-view of the training sessions.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body><KeyNames mac={process.platform === "darwin"}>{children}</KeyNames></body>
    </html>
  );
}
