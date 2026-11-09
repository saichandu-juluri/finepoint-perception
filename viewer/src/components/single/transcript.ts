// The transcript editor's state machine. Pure and free of value imports, so tests/editor-check.mjs can run it in Node.
import type { Cue } from "../../lib/srt";

export type EdCue = Cue & { id: number };
export type Status = "saved" | "dirty" | "saving" | "conflict" | "error";
type Snap = { cues: EdCue[]; editing: number | null };

export type EdState = {
  cues: EdCue[]; // always sorted by start, then end
  savedKeys: Record<number, string>; // id -> start|end|text of the version on disk
  nextId: number;
  baseMtimeMs: number | null; // modified time of the .srt this state is based on; null = no file yet
  exists: boolean;
  status: Status;
  error: string | null;
  errorCue: number | null;
  diskMtimeMs: number | null; // the newer version found on disk (conflict)
  past: Snap[];
  future: Snap[];
  editing: number | null;
  typing: number | null; // consecutive keystrokes in this cue form one undo step
  query: string;
};

export type Action =
  | { type: "load"; cues: Cue[]; mtimeMs: number | null; exists: boolean; keepHistory?: boolean }
  | { type: "replace"; cues: Cue[] }
  | { type: "edit"; id: number | null }
  | { type: "text"; id: number; text: string }
  | { type: "cancel" }
  | { type: "next" }
  | { type: "times"; id: number; startMs?: number; endMs?: number }
  | { type: "split"; id: number; atMs: number; caret?: number | null }
  | { type: "merge"; id: number }
  | { type: "delete"; id: number }
  | { type: "insertAfter"; id: number; atMs: number; fileMs: number }
  | { type: "newAt"; atMs: number; fileMs: number }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "query"; q: string }
  | { type: "saving" }
  | { type: "saved"; cues: EdCue[]; mtimeMs: number }
  | { type: "rebase"; mtimeMs: number | null; exists: boolean }
  | { type: "conflict"; mtimeMs: number | null }
  | { type: "failed"; error: string; cueId?: number | null };

const HISTORY = 200, NEW_MS = 2000, MIN_ROOM_MS = 100;
// The text as normalizeCues in srt.ts writes it (tests/editor-check.mjs holds the two together), so spaces at line
// ends, which saving drops anyway, never count as a change.
export const cleanText = (text: string) => text.replace(/\r\n?/g, "\n").split("\n").map(l => l.trim()).filter(Boolean).join("\n").replaceAll("-->", "->");
const keyOf = (c: Cue) => `${c.startMs}|${c.endMs}|${cleanText(c.text)}`;
const order = (a: Cue, b: Cue) => a.startMs - b.startMs || a.endMs - b.endMs;
const sorted = (cues: EdCue[]) => [...cues].sort(order);

export function init(cues: Cue[], mtimeMs: number | null, exists: boolean): EdState {
  const withIds = [...cues].sort(order).map((c, i) => ({ ...c, id: i + 1 }));
  return {
    cues: withIds, savedKeys: Object.fromEntries(withIds.map(c => [c.id, keyOf(c)])), nextId: withIds.length + 1,
    baseMtimeMs: mtimeMs, exists, status: "saved", error: null, errorCue: null, diskMtimeMs: null,
    past: [], future: [], editing: null, typing: null, query: "",
  };
}

const snap = (s: EdState): Snap => ({ cues: s.cues, editing: s.editing });

export const isChanged = (s: EdState, c: EdCue) => s.savedKeys[c.id] !== keyOf(c);

export function changeCount(s: EdState): number {
  const ids = new Set(s.cues.map(c => c.id));
  return s.cues.filter(c => isChanged(s, c)).length + Object.keys(s.savedKeys).filter(id => !ids.has(+id)).length;
}

// True when `cues` are exactly the version this state was loaded from or last saved, in any order. A program that
// rewrites the file with the same words moves only the file's time.
export function sameAsSaved(s: EdState, cues: Cue[]): boolean {
  const a = Object.values(s.savedKeys).sort(), b = cues.map(keyOf).sort();
  return a.length === b.length && a.every((k, i) => k === b[i]);
}

// Red problems block saving; an overlap with the next cue is only a warning.
export function problem(c: Cue): string | null {
  if (!Number.isFinite(c.startMs) || c.startMs < 0) return "It has no valid start time.";
  if (c.endMs <= c.startMs) return "It ends before it starts.";
  if (!c.text.trim()) return "It has no text.";
  return null;
}
export const overlapMs = (cues: Cue[], i: number) => (i + 1 < cues.length ? Math.max(0, cues[i].endMs - cues[i + 1].startMs) : 0);

// The speaker label in front of a cue ("S1: ", "S?: "), as speakerOf in lib/srt.ts reads it.
const LABEL = /^S(?:\d+|\?):\s/;

// Where to cut a cue's text: at the caret when it is inside the text, else at the word boundary nearest the middle.
// A label stays in front of both halves.
export function splitText(text: string, caret?: number | null): [string, string] {
  const head = text.match(LABEL)?.[0];
  if (head) return splitText(text.slice(head.length), caret != null ? caret - head.length : null).map(t => (t ? head + t : t)) as [string, string];
  if (caret != null && caret > 0 && caret < text.length) return [text.slice(0, caret).trimEnd(), text.slice(caret).trimStart()];
  const words = text.trim().split(/\s+/);
  if (words.length < 2) return [text.trim(), ""];
  let best = 1, bestDiff = Infinity, half = text.trim().length / 2;
  for (let k = 1; k < words.length; k++) {
    const d = Math.abs(words.slice(0, k).join(" ").length - half);
    if (d < bestDiff) { bestDiff = d; best = k; }
  }
  return [words.slice(0, best).join(" "), words.slice(best).join(" ")];
}

function settle(s: EdState, cues: EdCue[], patch: Partial<EdState> = {}): EdState {
  const next = { ...s, ...patch, cues };
  const clean = next.cues.length === Object.keys(next.savedKeys).length && next.cues.every(c => !isChanged(next, c));
  return { ...next, status: s.status === "conflict" ? "conflict" : s.status === "saving" ? "saving" : clean ? "saved" : "dirty", error: s.status === "conflict" ? s.error : null, errorCue: null };
}

// A change that can be undone: the state before it goes on the undo stack.
function commit(s: EdState, cues: EdCue[], patch: Partial<EdState> = {}): EdState {
  return settle({ ...s, past: [...s.past, snap(s)].slice(-HISTORY), future: [], typing: null }, sorted(cues), patch);
}

function newCue(s: EdState, startMs: number, fileMs: number) {
  startMs = Math.max(0, Math.min(Math.round(startMs), Math.max(0, fileMs - MIN_ROOM_MS)));
  const nextStart = s.cues.find(c => c.startMs > startMs)?.startMs ?? fileMs;
  let endMs = Math.min(startMs + NEW_MS, nextStart, fileMs);
  if (endMs - startMs < MIN_ROOM_MS) endMs = Math.min(startMs + NEW_MS, Math.max(fileMs, startMs + MIN_ROOM_MS)); // no room: overlap, shown amber
  const cue: EdCue = { id: s.nextId, startMs, endMs, text: "" };
  return commit({ ...s, nextId: s.nextId + 1 }, [...s.cues, cue], { editing: cue.id });
}

export function reducer(s: EdState, a: Action): EdState {
  const at = (id: number) => s.cues.findIndex(c => c.id === id);
  switch (a.type) {
    case "load": {
      const fresh = init(a.cues, a.mtimeMs, a.exists);
      const ids = fresh.cues.map(c => ({ ...c, id: c.id + s.nextId }));
      const base = { ...fresh, cues: ids, savedKeys: Object.fromEntries(ids.map(c => [c.id, keyOf(c)])), nextId: s.nextId + ids.length + 1, query: s.query };
      return a.keepHistory ? { ...base, past: [...s.past, snap(s)].slice(-HISTORY) } : base;
    }
    case "replace": {
      let id = s.nextId;
      const cues = a.cues.map(c => ({ ...c, id: id++ }));
      return commit({ ...s, nextId: id }, cues, { editing: null });
    }
    case "edit":
      return a.id === s.editing ? s : { ...s, editing: a.id != null && at(a.id) >= 0 ? a.id : null, typing: null };
    case "text": {
      const i = at(a.id);
      if (i < 0 || s.cues[i].text === a.text) return s;
      const cues = s.cues.map(c => (c.id === a.id ? { ...c, text: a.text } : c));
      if (s.typing === a.id) return settle(s, cues);
      return { ...commit(s, cues), typing: a.id };
    }
    case "cancel": {
      if (s.typing != null && s.typing === s.editing && s.past.length) {
        const prev = s.past[s.past.length - 1];
        return settle({ ...s, past: s.past.slice(0, -1), future: [snap(s), ...s.future].slice(0, HISTORY), typing: null }, prev.cues, { editing: null });
      }
      return { ...s, editing: null, typing: null };
    }
    case "next": {
      const i = s.editing == null ? -1 : at(s.editing);
      return { ...s, editing: i >= 0 && i + 1 < s.cues.length ? s.cues[i + 1].id : null, typing: null };
    }
    case "times": {
      const i = at(a.id);
      if (i < 0) return s;
      const c = s.cues[i], startMs = Math.round(a.startMs ?? c.startMs), endMs = Math.round(a.endMs ?? c.endMs);
      if (startMs === c.startMs && endMs === c.endMs) return s;
      return commit(s, s.cues.map(x => (x.id === a.id ? { ...x, startMs, endMs } : x)));
    }
    case "split": {
      const i = at(a.id), c = s.cues[i], atMs = Math.round(a.atMs);
      if (i < 0 || atMs <= c.startMs || atMs >= c.endMs) return s;
      const [first, second] = splitText(c.text, a.caret);
      const tail: EdCue = { id: s.nextId, startMs: atMs, endMs: c.endMs, text: second };
      return commit({ ...s, nextId: s.nextId + 1 }, [...s.cues.map(x => (x.id === a.id ? { ...x, endMs: atMs, text: first } : x)), tail]);
    }
    case "merge": {
      const i = at(a.id);
      if (i < 0 || i + 1 >= s.cues.length) return s;
      const c = s.cues[i], n = s.cues[i + 1];
      // The same speaker's label is written once; another speaker's stays where that speaker starts.
      const first = c.text.trim(), next = n.text.trim(), lf = first.match(LABEL)?.[0], ln = next.match(LABEL)?.[0];
      const merged = { ...c, endMs: Math.max(c.endMs, n.endMs), text: [first, lf && ln && lf.trim() === ln.trim() ? next.slice(ln.length) : next].filter(Boolean).join(" ") };
      return commit(s, s.cues.filter(x => x.id !== n.id).map(x => (x.id === c.id ? merged : x)), { editing: s.editing === n.id ? c.id : s.editing });
    }
    case "delete": {
      if (at(a.id) < 0) return s;
      return commit(s, s.cues.filter(x => x.id !== a.id), { editing: s.editing === a.id ? null : s.editing });
    }
    case "insertAfter": {
      const i = at(a.id);
      return i < 0 ? s : newCue(s, Math.max(s.cues[i].endMs, a.atMs), a.fileMs);
    }
    case "newAt":
      return newCue(s, a.atMs, a.fileMs);
    case "undo": {
      const prev = s.past[s.past.length - 1];
      if (!prev) return s;
      return settle({ ...s, past: s.past.slice(0, -1), future: [snap(s), ...s.future].slice(0, HISTORY), typing: null }, prev.cues, { editing: prev.editing });
    }
    case "redo": {
      const next = s.future[0];
      if (!next) return s;
      return settle({ ...s, past: [...s.past, snap(s)].slice(-HISTORY), future: s.future.slice(1), typing: null }, next.cues, { editing: next.editing });
    }
    case "query":
      return { ...s, query: a.q };
    case "saving":
      return { ...s, status: "saving", error: null, errorCue: null };
    case "saved": {
      // The editor keeps its own cues (the one being typed in included): edits made while saving stay unsaved.
      const savedKeys = Object.fromEntries(a.cues.map(c => [c.id, keyOf(c)]));
      return settle({ ...s, status: "saved", savedKeys, baseMtimeMs: a.mtimeMs, exists: true, diskMtimeMs: null, typing: null }, s.cues);
    }
    case "rebase":
      return { ...s, baseMtimeMs: a.mtimeMs, exists: a.exists };
    case "conflict":
      return { ...s, status: "conflict", diskMtimeMs: a.mtimeMs, error: null, errorCue: null };
    case "failed":
      return { ...s, status: "error", error: a.error, errorCue: a.cueId ?? null };
  }
}
