"use client";
// The names of the keys as this computer's keyboard shows them: ⌘ ⌥ ⇧ Return on a Mac, Ctrl Alt Shift Enter on Windows.
// The root layout gives the server's platform, which is this computer's (the app answers only on 127.0.0.1).
import { createContext, useContext, type ReactNode } from "react";

const Mac = createContext(true);
export const KeyNames = ({ mac, children }: { mac: boolean; children: ReactNode }) => <Mac.Provider value={mac}>{children}</Mac.Provider>;

const MAC: Record<string, string> = { cmd: "⌘", shift: "⇧", alt: "⌥", return: "Return" };
const PC: Record<string, string> = { cmd: "Ctrl", shift: "Shift", alt: "Alt", return: "Enter" };
const ORDER = ["cmd", "alt", "shift"];

// key("shift+cmd+Z"): "⇧⌘Z" on a Mac, "Ctrl+Shift+Z" elsewhere.
export function useKey() {
  const mac = useContext(Mac);
  return (keys: string) => {
    const parts = keys.split("+");
    if (mac) return parts.map(p => MAC[p] ?? p).join("");
    return parts.sort((a, b) => (ORDER.indexOf(a) + 1 || 9) - (ORDER.indexOf(b) + 1 || 9)).map(p => PC[p] ?? p).join("+");
  };
}
