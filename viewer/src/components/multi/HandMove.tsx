"use client";
// The parts only the person can place (left out of the clock, or placed without sound as a first guess), the parts the
// clock step flagged, and the hand move: slide one part, or a whole camera, earlier or later on the clock while all
// cameras play, type its start, or mark the same moment in a placed camera and in the part. Save writes decisions.json
// clock.user.manual["cam/part"] = {start_s, file, note} for each part moved, and "Rerun the clock" applies it.
// The move is shown at once by moving the parts on the page's own clock (moveTo); nothing is written until Save.
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtClock, parseClock } from "@/lib/srt";
import { num } from "@/lib/format";
import { frameAt, movable as canMove, moveTo, partAt, type Part, type Session } from "@/lib/session";
import type { RunState } from "@/server/run";
import { Icon } from "../Icon";
import { useRun } from "../RunPanel";
import type { OneClock } from "./oneClock";

const ms = (s: number) => `${s >= 0 ? "+" : "−"}${num(Math.abs(s) * 1000, 1)} ms`;
const round = (s: number) => Math.round(s * 1e6) / 1e6;
const PLACED: Record<string, string> = { sound: "sound shared with other cameras", chained: "following the part before, as a first guess", motion: "picture motion" };
type Target = { key: string; p: Part; start: number };
type Marks = { placed: number | null; here: { clock: number; frame: number } | null };

export function HandMove({ job, s, ctl, initialPart, run: initialRun }: { job: string; s: Session; ctl: OneClock | null; initialPart: string | null; run: RunState }) {
  const movable = s.cameras.flatMap(c => c.parts.filter(p => canMove(s, c, p)).map(p => ({ key: `${c.key}/${p.key}`, cam: c, p })));
  const find = (k: string | null) => movable.find(m => m.key === k) ?? null;
  const targets = (k: string | null, whole: boolean): Target[] => {
    const m = find(k);
    return (m ? (whole ? movable.filter(x => x.cam === m.cam) : [m]) : []).map(x => ({ key: x.key, p: x.p, start: x.p.start_s }));
  };
  const firstKey = () => [initialPart, ...s.needs, ...s.flags.map(f => f.key), movable[0]?.key].find(k => find(k ?? null)) ?? null;
  const [key, setKey] = useState<string | null>(firstKey);
  const [whole, setWhole] = useState(false);
  const [delta, setDelta] = useState(0);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, number>>({}); // saved here since this session came from the server
  const [marks, setMarks] = useState<Marks>({ placed: null, here: null });
  const base = useRef<{ key: string | null; whole: boolean; list: Target[] }>({ key: null, whole: false, list: [] });
  const run = useRun(job, initialRun), clockRun = run.st?.job?.only === "clock" ? run.st.job : null;
  // each part's start as the server gave it, before any slide moves it on the page
  const guess = useMemo(() => Object.fromEntries(s.cameras.flatMap(c => c.parts.map(p => [`${c.key}/${p.key}`, p.start_s]))), [s]);
  const sel = find(key), part = sel?.p ?? null, locked = !!s.clock.existing;
  const wholeOk = !!sel && sel.cam.parts.every(p => canMove(s, sel.cam, p));

  // A new session from the server (after a clock rerun) starts from its own starts, with no slide.
  useEffect(() => {
    base.current = { key, whole, list: targets(key, whole) };
    setDelta(0); setSaved({}); setMarks({ placed: null, here: null });
  }, [s]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = (list: Target[], d: number) => { list.forEach(t => moveTo(t.p, t.start + d)); ctl?.showClock(ctl.shownClock()); };
  const pick = (k: string, w: boolean, go = false) => {
    apply(base.current.list, 0); // an unsaved slide of the parts before is put back
    base.current = { key: k, whole: w, list: targets(k, w) };
    setKey(k); setWhole(w); setDelta(0); setMsg(null); setMarks({ placed: null, here: null });
    const p = find(k)?.p;
    if (go && p) ctl?.showClock(p.clock_from); // the part's split: where it meets the part before
  };
  const to = (d: number) => {
    if (!part || base.current.key !== key) return;
    const next = round(d);
    setDelta(next); setMarks({ placed: null, here: null });
    apply(base.current.list, next);
  };
  const selStart = () => base.current.list.find(t => t.key === key)?.start ?? 0;

  // The same moment, marked in a placed camera (clock c) and in the part (its frame f): the part then starts at c − f frames.
  const lineUp = (m: Marks) => {
    if (m.placed == null || !m.here || !part) { setMarks(m); return; }
    to(m.placed - m.here.frame * part.s_per_frame - selStart());
    setMsg({ text: `Lined up: frame ${num(m.here.frame)} of ${key} now meets clock ${fmtClock(m.placed, 3)}, the moment marked in a placed camera. Play to check, then Save.` });
  };
  const markPlaced = () => {
    if (!ctl || !sel) return;
    const c = ctl.shownClock(), seen = s.cameras.some(cam => cam !== sel.cam && (() => { const q = partAt(cam, c); return q && !s.needs.includes(`${cam.key}/${q.key}`); })());
    if (!seen) { setMsg({ text: "No placed camera records at this moment. Go to a moment a placed camera shows, such as a clap, then mark it.", bad: true }); return; }
    setMsg(null);
    lineUp({ ...marks, placed: c });
  };
  const markHere = () => {
    if (!ctl || !sel || !part) return;
    const c = ctl.shownClock(), q = partAt(sel.cam, c);
    if (q !== part) { setMsg({ text: `${key} does not show at this moment. Go to the same moment in ${key}, then mark it.`, bad: true }); return; }
    setMsg(null);
    lineUp({ ...marks, here: { clock: c, frame: frameAt(sel.cam, part, c) } });
  };

  const save = async () => {
    if (!part || !key) return;
    const list = base.current.list, moves = Object.fromEntries(list.map(t => [t.key, round(t.start + delta)]));
    const r = await fetch("/api/manual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session: job, moves, note }) }).catch(() => null);
    const j = await r?.json().catch(() => null);
    if (!r?.ok) { setMsg({ text: j?.error ?? "Not saved: the server did not answer.", bad: true }); return; }
    list.forEach(t => { t.start = moves[t.key]; });
    setSaved(v => ({ ...v, ...moves })); setSavedKey(key); setDelta(0);
    setMsg({ text: `Saved: ${list.length > 1 ? `the ${list.length} parts of ${sel!.cam.key}, ${key} at` : `${key} starts at`} clock ${fmtClock(moves[key], 3)} (${ms(delta)} from where it was). It counts once the clock step runs again.` });
  };
  const remove = async () => {
    if (!key) return;
    const r = await fetch(`/api/manual?session=${encodeURIComponent(job)}&part=${encodeURIComponent(key)}`, { method: "DELETE" }).catch(() => null);
    const j = await r?.json().catch(() => null);
    setMsg(r?.ok ? { text: `The hand setting of ${key} is removed. Rerun the clock to go back to the automatic start.` } : { text: j?.error ?? "The server did not answer.", bad: true });
    if (r?.ok) setSavedKey(key);
  };

  const fps = part ? part.fps / part.rate : 60, frame = 1 / fps;
  const savedAt = key ? saved[key] ?? s.manual[key]?.start_s : undefined;
  const state = !key ? null : part?.source === "manual" && !(key in saved) ? (s.manual[key] ? "applied" : "removed") : savedAt != null ? "saved" : null;
  const flags = s.flags.filter(f => !s.needs.includes(f.key));
  const step = (d: number, label: string, attr: string, title: string) => (
    <button type="button" className="btn secondary sm" onClick={() => to(delta + d)} {...{ [attr]: "" }} title={title}>{label}</button>
  );
  return (
    <section className="card box hand-move" aria-labelledby="h-check" data-hand-move>
      {s.needs.length > 0 && (
        <>
          <h2 id="h-needs">To place by hand</h2>
          <p className="note">The clock step could not place these: each has no sound, or its sound matched no other camera, so nothing lines it up with the others. Each is shown at a first guess; move it until it agrees with a placed camera, then Save and rerun the clock.</p>
          <div className="needs" data-needs={s.needs.length}>
            {s.needs.map(k => {
              const m = find(k), [cam, p] = k.split("/");
              return (
                <button type="button" key={k} className="need-btn" aria-pressed={k === key} data-need={k} disabled={!m} onClick={() => pick(k, whole && m?.cam === sel?.cam, true)}>
                  <Icon name="flag" />{cam} {p}
                  <span>· {m?.p.source === "unplaced" ? "left out of the clock" : "no sound, placed beside the part before"} · first guess {fmtClock(guess[k] ?? 0, 2)}{saved[k] != null || s.manual[k] ? ` · saved at ${fmtClock(saved[k] ?? s.manual[k].start_s, 2)}` : ""}</span>
                </button>
              );
            })}
          </div>
        </>
      )}
      <h2 id="h-check">Parts to check</h2>
      {flags.length ? (
        <div className="flags" data-flags>
          {flags.map(f => (
            <button type="button" key={f.key} className="flag-btn" aria-pressed={f.key === key} data-flag={f.key} title={`${f.message}${f.fix ? `. What to do: ${f.fix}` : ""}`}
              onClick={() => pick(f.key, false, true)}>
              <Icon name="flag" />{f.camera} {f.part}<span style={{ fontWeight: 400 }}>· {f.message}</span>
            </button>
          ))}
        </div>
      ) : <p className="note" data-flags-none>The clock step flagged no {s.needs.length ? "other " : ""}part{s.clock.existing ? " (this session keeps a clock made earlier)" : ""}.</p>}
      {Object.keys(s.manual).length > 0 && <p className="note" data-manual-list>Moved by hand: {Object.entries(s.manual).map(([k, v]) => `${k} to ${fmtClock(v.start_s, 3)}`).join(", ")}.</p>}

      {locked ? (
        <p className="note" data-locked>This session keeps a clock made earlier (<span className="mono">[clock] existing</span> in its project file), so a part cannot be moved by hand here. To move parts by hand, take that line out of project.toml and run the clock step: it then syncs every camera itself.</p>
      ) : part && key && sel && (
        <>
          <h3 style={{ marginTop: 12 }}>Move a part by hand</h3>
          <p className="note">Play the cameras, then slide the part until it agrees with the others: a clap, a word or a hand movement seen and heard at the same moment. Earlier makes it start sooner on the clock. Or mark that moment twice: once where a placed camera shows it, once where this part shows it.</p>
          <div className="slide" data-slide>
            <select aria-label="Part to move" value={key} onChange={e => pick(e.target.value, false, true)} data-slide-part>
              {movable.map(m => <option key={m.key} value={m.key}>{m.key}{s.needs.includes(m.key) ? " (to place)" : s.flags.some(f => f.key === m.key) ? " (flagged)" : ""}</option>)}
            </select>
            <label className="mark-row" title={wholeOk ? "Move every part of this camera by the same amount" : "A part of this camera is placed as the reference, so its parts move one at a time"}>
              <input type="checkbox" checked={whole} disabled={!wholeOk} onChange={e => pick(key, e.target.checked)} data-slide-whole />Whole camera
            </label>
            {step(-10, "−10 s", "data-slide-10s-back", "10 s earlier")}
            {step(-1, "−1 s", "data-slide-1s-back", "1 s earlier")}
            <button type="button" className="btn secondary sm" onClick={() => to(delta - frame)} data-slide-frame-back title="One frame earlier"><Icon name="fback" />1 frame</button>
            {step(-0.01, "−10 ms", "data-slide-10ms-back", "10 ms earlier")}
            <span className="delta" data-slide-delta={delta}>{ms(delta)}</span>
            {step(0.01, "+10 ms", "data-slide-10ms-fwd", "10 ms later")}
            <button type="button" className="btn secondary sm" onClick={() => to(delta + frame)} data-slide-frame-fwd title="One frame later">1 frame<Icon name="ffwd" /></button>
            {step(1, "+1 s", "data-slide-1s-fwd", "1 s later")}
            {step(10, "+10 s", "data-slide-10s-fwd", "10 s later")}
            <button type="button" className="btn ghost sm" onClick={() => to(0)} disabled={!delta}>Back to 0</button>
          </div>
          <div className="slide" data-slide-to>
            <StartField start={selStart() + delta} onGo={c => to(c - selStart())} />
            <button type="button" className="btn secondary sm" onClick={markPlaced} data-mark-placed={marks.placed ?? ""} title="The moment shown now, in a camera that is already placed"><Icon name="target" />Mark it in a placed camera</button>
            <button type="button" className="btn secondary sm" onClick={markHere} data-mark-here={marks.here?.frame ?? ""} title={`The same moment, as ${key} shows it now`}><Icon name="target" />Mark it in {key}</button>
            <span className="mark-row" data-marks>
              {marks.placed != null && <span>Placed camera marked at {fmtClock(marks.placed, 3)}.</span>}
              {marks.here && <span>{key} marked at its frame {num(marks.here.frame)}.</span>}
              {(marks.placed == null) !== !marks.here && <span>Now the other one.</span>}
            </span>
          </div>
          <div className="slide">
            <input type="text" placeholder="Note (what you lined up)" value={note} onChange={e => setNote(e.target.value)} aria-label="Note" style={{ minWidth: 220 }} data-slide-note />
            <button type="button" className="btn primary sm" onClick={save} disabled={!delta && savedAt != null} data-slide-save>Save</button>
            {s.manual[key] && <button type="button" className="btn ghost sm" onClick={remove} data-slide-remove>Remove the hand setting</button>}
          </div>
          <p className="note" data-slide-where>
            {key} starts at clock <span className="mono">{fmtClock(selStart() + delta, 3)}</span> ({part.source === "manual" ? "moved by hand" : part.source === "unplaced" ? "a first guess: not on the clock yet" : `placed by ${PLACED[part.source] ?? (part.source || "the clock step")}`}); one frame is {num(frame * 1000, 2)} ms.
            {whole && <span> Every part of {sel.cam.key} moves with it ({base.current.list.length}).</span>}
            {state === "applied" && <span className="moved" data-moved> <Icon name="check" />Moved by hand: this start is the saved one{s.manual[key]?.note ? ` (${s.manual[key].note})` : ""}.</span>}
            {state === "saved" && <span className="moved" data-moved-pending> Saved by hand at {fmtClock(savedAt!, 3)}; rerun the clock to apply it.</span>}
          </p>
          {(msg || savedKey || clockRun) && (
            <div className="run-bar" data-rerun>
              {msg && <span className={`run-msg${msg.bad ? " is-err" : ""}`} data-slide-msg>{msg.text}</span>}
              {(savedKey || state === "saved") && !run.running && <button type="button" className="btn primary sm" onClick={() => { setMsg(null); run.start("clock"); }} disabled={run.st?.busy} data-rerun-clock><Icon name="refresh" />Rerun the clock</button>}
              {clockRun?.status === "running" && <span className="run-msg" data-rerun-state="running">Rerunning the clock…</span>}
              {clockRun && clockRun.status !== "running" && <span className={`run-msg${clockRun.status === "failed" ? " is-err" : ""}`} data-rerun-state={clockRun.status}>
                {clockRun.status === "done" ? "The clock was rerun; the cameras now follow the new clock."
                  : clockRun.status === "cancelled" ? "The clock rerun was cancelled." : `The clock rerun stopped: ${clockRun.error}`}</span>}
              {run.running && !clockRun && <span className="run-msg" data-run-later-state="running">Running every step that changed. The overview shows each step as it goes.</span>}
              {run.error && <span className="run-msg is-err">{run.error}</span>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

// The part's start typed as a clock time (45:00.250, or seconds), applied on Return.
function StartField({ start, onGo }: { start: number; onGo: (c: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const go = (text: string) => {
    const c = parseClock(text);
    if (c == null) { setBad(true); setTimeout(() => setBad(false), 1200); return; }
    setDraft(null);
    onGo(c);
  };
  return (
    <label className="mark-row">Start at
      <input className={`time-in${bad ? " bad" : ""}`} aria-label="Start of the part on the session clock" spellCheck={false} autoComplete="off" data-slide-start
        value={draft ?? fmtClock(start, 3)} onFocus={e => { setDraft(e.currentTarget.value); requestAnimationFrame(() => e.target.select()); }}
        onChange={e => setDraft(e.target.value)} onBlur={() => setDraft(null)}
        onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); go(e.currentTarget.value); } if (e.key === "Escape") { setDraft(null); e.currentTarget.blur(); } }} />
    </label>
  );
}

