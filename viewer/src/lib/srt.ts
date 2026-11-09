// Transcript files: standard SRT beside each video (times from the start of that video), and the .txt reading version.
// Pure, no imports: the app and the tests (plain Node) both load it.

export type Cue = { startMs: number; endMs: number; text: string };

const pad = (n: number, width = 2) => String(n).padStart(width, "0");

export function parseTimestamp(s: string): number | null {
  const m = s.trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})$/);
  if (!m) return null;
  const [, h = "0", min, sec, frac] = m;
  return ((+h * 60 + +min) * 60 + +sec) * 1000 + +frac.padEnd(3, "0");
}

export function formatSrtTime(ms: number): string {
  ms = Math.max(0, Math.round(ms));
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}

// Reads SRT and WebVTT alike. Blocks without a valid timing line or without text are counted in `skipped`.
export function parseSrt(text: string): { cues: Cue[]; skipped: number } {
  const cues: Cue[] = [];
  let skipped = 0;
  for (const block of text.replace(/^﻿/, "").replace(/\r\n?/g, "\n").split(/\n[ \t]*\n/)) {
    const lines = block.split("\n");
    while (lines.length && !lines[0].trim()) lines.shift();
    if (!lines.length) continue;
    const at = lines.findIndex(l => l.includes("-->"));
    if (at < 0) {
      if (!/^(WEBVTT|NOTE|STYLE|REGION)\b/.test(lines[0])) skipped++;
      continue;
    }
    const m = lines[at].match(/^\s*(\S+)\s+-->\s+(\S+)/);
    const startMs = m ? parseTimestamp(m[1]) : null, endMs = m ? parseTimestamp(m[2]) : null;
    const body = lines.slice(at + 1).join("\n").replace(/\s+$/, "");
    if (startMs == null || endMs == null || !body.trim()) { skipped++; continue; }
    cues.push({ startMs, endMs, text: body });
  }
  return { cues, skipped };
}

export function formatSrt(cues: Cue[]): string {
  return cues.map((c, i) => `${i + 1}\n${formatSrtTime(c.startMs)} --> ${formatSrtTime(c.endMs)}\n${c.text}\n\n`).join("");
}

// The speaker label in front of a cue: "S1: " (S1 talks most in that video), "S?: " where the speaker is not known.
// `head` is the label with its colon and space, `rest` the words after it.
export function speakerOf(text: string): { label: string; head: string; rest: string } | null {
  const m = text.match(/^(S(?:\d+|\?)):\s/);
  return m ? { label: m[1], head: m[0], rest: text.slice(m[0].length) } : null;
}

// The reading version: a cue joins the paragraph before it when the same speaker goes on (or neither has a label), it
// starts at most 1.5 s after it and the paragraph's words are still under 200 characters. The label is written once,
// at the start of its paragraph; minutes keep counting past an hour ("[75:30]").
export function formatTxt(cues: Cue[]): string {
  const stamp = (ms: number) => { const t = Math.floor(ms / 1000); return `${Math.floor(t / 60)}:${pad(t % 60)}`; };
  const paras: { speaker: string | null; startMs: number; endMs: number; text: string }[] = [];
  for (const c of cues) {
    const flat = c.text.replace(/\s*\n\s*/g, " "), s = speakerOf(flat), speaker = s?.label ?? null, text = s ? s.rest : flat;
    const last = paras[paras.length - 1];
    if (last && last.speaker === speaker && c.startMs - last.endMs <= 1500 && last.text.length < 200) {
      last.endMs = c.endMs;
      last.text += " " + text;
    } else paras.push({ speaker, startMs: c.startMs, endMs: c.endMs, text });
  }
  return paras.map(p => `[${stamp(p.startMs)}] ${p.speaker ? `${p.speaker}: ` : ""}${p.text}\n`).join("");
}

// Cleans cues for saving: sorted, whole milliseconds, one line break at most between lines (a blank line would end the
// SRT block), no "-->" inside text. Returns the reason instead when a cue cannot be saved.
export function normalizeCues(cues: Cue[]): { cues: Cue[] } | { error: string } {
  const out: Cue[] = [];
  for (const [i, c] of cues.entries()) {
    const startMs = Math.round(c.startMs), endMs = Math.round(c.endMs);
    const text = String(c.text ?? "").replace(/\r\n?/g, "\n").split("\n").map(l => l.trim()).filter(Boolean).join("\n").replaceAll("-->", "->");
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs < 0) return { error: `Line ${i + 1} has no valid start time.` };
    if (endMs <= startMs) return { error: `Line ${i + 1} ends before it starts.` };
    if (!text) return { error: `Line ${i + 1} has no text.` };
    out.push({ startMs, endMs, text });
  }
  return { cues: out.sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs) };
}

// The cue on screen at `ms`: the last one that has started and not yet ended, or -1.
export function cueIndexAt(cues: Cue[], ms: number): number {
  for (let i = cues.length - 1; i >= 0; i--) if (cues[i].startMs <= ms) return ms < cues[i].endMs ? i : -1;
  return -1;
}

// Session-clock times as the job page writes them: m:ss.d, a real minus sign, never 60 seconds.
export function fmtClock(c: number | null | undefined, d = 1): string {
  if (c == null) return "";
  const neg = c < 0;
  c = Math.round(Math.abs(c) * 10 ** d) / 10 ** d;
  const m = Math.floor(c / 60);
  return `${neg ? "−" : ""}${m}:${(c % 60).toFixed(d).padStart(d ? 3 + d : 2, "0")}`;
}

// "75:30.5", "1:15:30.5", "01:15:30,500" as in an .srt, or plain seconds; anything else is null.
export function parseClock(s: string): number | null {
  const t = String(s).trim().replace("−", "-");
  const m = t.match(/^(-)?(?:(\d+):)?(\d+):(\d+(?:[.,]\d+)?)$/);
  if (m) return (m[1] ? -1 : 1) * (3600 * +(m[2] ?? 0) + 60 * +m[3] + +m[4].replace(",", "."));
  const v = t ? Number(t) : NaN;
  return Number.isFinite(v) ? v : null;
}
