// A cue's text with its speaker label ("S1:", as transcripts with speakers carry it) in that speaker's colour. The text
// is written out unchanged, so the element's text is still the cue's text.
import type { ReactNode } from "react";
import { speakerOf } from "@/lib/srt";

export function Spoken({ text, mark = t => t }: { text: string; mark?: (t: string) => ReactNode }) {
  const s = speakerOf(text);
  if (!s) return <>{mark(text)}</>;
  const n = s.label === "S?" ? 0 : ((+s.label.slice(1) - 1) % 8) + 1; // eight colours, then they repeat
  return <><span className="spk" data-spk={n || "unknown"}>{s.label}:</span>{s.head.slice(s.label.length + 1)}{mark(s.rest)}</>;
}
