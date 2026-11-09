// The transcript editor's state machine without a browser:  node tests/editor-check.mjs
import { changeCount, cleanText, init, overlapMs, problem, reducer, sameAsSaved, splitText } from "../src/components/single/transcript.ts";
import { normalizeCues } from "../src/lib/srt.ts";

let failed = 0;
const check = (ok, what) => { console.log(`  ${ok ? "ok  " : "FAIL"}  ${what}`); if (!ok) failed++; };
const run = (s, ...actions) => actions.reduce(reducer, s);
const cues = [
  { startMs: 1000, endMs: 3000, text: "one two three four" },
  { startMs: 4000, endMs: 5000, text: "five" },
  { startMs: 8000, endMs: 9000, text: "six" },
];
const s0 = init(cues, 123.5, true);
check(s0.status === "saved" && changeCount(s0) === 0 && s0.cues.map(c => c.id).join() === "1,2,3", "a loaded transcript starts saved, with ids");

const typed = run(s0, { type: "edit", id: 2 }, { type: "text", id: 2, text: "fiv" }, { type: "text", id: 2, text: "fivX" });
check(typed.status === "dirty" && changeCount(typed) === 1 && typed.past.length === 1, "typing makes it dirty and counts as one undo step");
check(run(typed, { type: "undo" }).status === "saved" && run(typed, { type: "undo" }, { type: "redo" }).cues[1].text === "fivX", "undo returns to saved, redo brings the typing back");
const esc = run(typed, { type: "cancel" });
check(esc.cues[1].text === "five" && esc.editing === null && esc.status === "saved", "Esc undoes the typing and stops editing");

const split = run(s0, { type: "split", id: 1, atMs: 2000 });
check(split.cues.length === 4 && split.cues[0].endMs === 2000 && split.cues[1].startMs === 2000 && split.cues[0].text === "one two" && split.cues[1].text === "three four", "split cuts the time at the playhead and the text at the middle word");
check(splitText("hello world", 5).join("|") === "hello|world" && splitText("solo").join("|") === "solo|", "split at the caret; one word stays whole");
check(splitText("S1: one two three four").join("|") === "S1: one two|S1: three four" && splitText("S2: hello world", 9).join("|") === "S2: hello|S2: world"
  && splitText("S?: solo").join("|") === "S?: solo|" && splitText("S1: hello world", 2).join("|") === "S1: hello|S1: world",
  "a speaker label (S1:) stays in front of both halves, the caret counts in the text, and a caret inside the label splits at the middle");
const spk = init([{ startMs: 0, endMs: 1000, text: "S1: one" }, { startMs: 1000, endMs: 2000, text: "S1: two" }, { startMs: 2000, endMs: 3000, text: "S2: three" }], 1, true);
check(run(spk, { type: "merge", id: 1 }).cues[0].text === "S1: one two" && run(spk, { type: "merge", id: 2 }).cues[1].text === "S1: two S2: three",
  "merging two lines of one speaker keeps one label; another speaker's label stays where that speaker starts");
check(run(s0, { type: "split", id: 1, atMs: 3500 }) === s0, "split outside the cue does nothing");
const merged = run(split, { type: "merge", id: split.cues[0].id });
check(merged.cues.length === 3 && merged.cues[0].text === "one two three four" && merged.cues[0].endMs === 3000, "merge joins the text and keeps the later end");

const ins = run(s0, { type: "insertAfter", id: 1, atMs: 2500, fileMs: 60000 });
const n = ins.cues.find(c => c.text === "");
check(n.startMs === 3000 && n.endMs === 4000 && ins.editing === n.id && overlapMs(ins.cues, ins.cues.indexOf(n)) === 0, "insert after starts at the cue's end and stops at the next cue");
const ins2 = run(s0, { type: "insertAfter", id: 2, atMs: 5200, fileMs: 60000 }).cues.find(c => c.text === "");
check(ins2.startMs === 5200 && ins2.endMs === 7200, "insert after starts at the playhead when it is later, 2 s long");
const tight = run(s0, { type: "newAt", atMs: 3950, fileMs: 60000 });
const t = tight.cues.find(c => c.text === "");
check(t.endMs - t.startMs === 2000 && overlapMs(tight.cues, tight.cues.indexOf(t)) > 0 && problem(t) === "It has no text.", "a new cue with no room overlaps (a warning) and needs text (an error)");
check(problem({ startMs: 5, endMs: 5, text: "x" }) === "It ends before it starts." && problem({ startMs: 0, endMs: 5, text: "x" }) === null, "problems are found");

const del = run(s0, { type: "delete", id: 3 });
check(del.cues.length === 2 && changeCount(del) === 1 && del.status === "dirty", "delete counts as a change");
const sent = typed.cues;
const saved = run(typed, { type: "saving" }, { type: "saved", cues: sent, mtimeMs: 200 });
check(saved.status === "saved" && saved.baseMtimeMs === 200 && changeCount(saved) === 0, "after saving the new file time is the base");
const during = run(typed, { type: "saving" }, { type: "text", id: 3, text: "seven" }, { type: "saved", cues: sent, mtimeMs: 201 });
check(during.status === "dirty" && changeCount(during) === 1, "an edit made while saving stays unsaved");
const conflict = run(typed, { type: "conflict", mtimeMs: 999 }, { type: "text", id: 1, text: "changed" });
check(conflict.status === "conflict" && conflict.diskMtimeMs === 999, "a conflict stays until it is resolved");
const reloaded = run(typed, { type: "load", cues, mtimeMs: 300, exists: true, keepHistory: true });
check(reloaded.status === "saved" && reloaded.baseMtimeMs === 300 && run(reloaded, { type: "undo" }).cues[1].text === "fivX", "loading the disk version can be undone back to my edits");
let many = s0;
for (let i = 0; i < 250; i++) many = reducer(many, { type: "times", id: 3, endMs: 9000 + i + 1 });
check(many.past.length === 200, "at least 100 undo steps are kept");

check(sameAsSaved(s0, [cues[2], cues[0], cues[1]]) && !sameAsSaved(s0, cues.slice(1)) && !sameAsSaved(s0, cues.map((c, i) => (i ? c : { ...c, text: "x" }))), "the same words on disk count as the saved version, in any order; any change does not");
check(sameAsSaved(init([], null, false), []) && sameAsSaved(run(typed, { type: "saved", cues: typed.cues, mtimeMs: 7 }), typed.cues), "an empty file matches no file; after a save the saved cues are the base");
const odd = [" a  \r\n\n  b --> c ", "x", "  ", "line one \nline two\n"];
check(odd.every(t => cleanText(t) === (normalizeCues([{ startMs: 0, endMs: 1, text: t }]).cues?.[0]?.text ?? "")), "the editor cleans text exactly as saving does");
const trailing = run(s0, { type: "edit", id: 1 }, { type: "text", id: 1, text: "one two three four " });
const savedTrailing = run(trailing, { type: "saving" }, { type: "saved", cues: trailing.cues.map(c => ({ ...c, text: cleanText(c.text) })), mtimeMs: 300 });
check(savedTrailing.status === "saved" && savedTrailing.cues[0].text === "one two three four ", "saving while typing keeps the typed text as it is (a space at the end stays) and counts as saved");
const unsorted = init([{ startMs: 5000, endMs: 9000, text: "later end" }, { startMs: 5000, endMs: 6000, text: "earlier end" }], 1, true);
check(unsorted.cues[0].text === "earlier end" && unsorted.cues[0].id === 1, "a file with cues out of order is sorted before ids are given");
check(run(typed, { type: "rebase", mtimeMs: 555, exists: true }).baseMtimeMs === 555 && changeCount(run(typed, { type: "rebase", mtimeMs: 555, exists: true })) === 1, "a rewrite with the same words only moves the base time; edits stay");

console.log(failed ? `FAIL (${failed})` : "PASS");
process.exit(failed ? 1 : 0);
