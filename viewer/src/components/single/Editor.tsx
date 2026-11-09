"use client";
// The transcript panel of the single view: toolbar, cue list with the edit card, conflict banner and the empty state.
import { memo, useEffect, useLayoutEffect, useRef, useState, type Dispatch, type ReactNode, type RefObject } from "react";
import { cueIndexAt, fmtClock, parseClock } from "@/lib/srt";
import { num } from "@/lib/format";
import { Icon } from "../Icon";
import { Spoken } from "../Spoken";
import { changeCount, isChanged, overlapMs, problem, type Action, type EdCue, type EdState } from "./transcript";
import { usePlayhead, type PlayheadStore, type VideoControls } from "./playhead";

export type EditorActions = {
  save: () => void; reload: () => void; loadTheirs: () => void; keepMine: () => void; download: () => void;
  pickFile: () => void; useFile: (f: File) => void; newCue: () => void; insertAfter: (id: number) => void;
};

const secs = (ms: number) => (ms / 1000).toFixed(2);
// Smooth for short moves; a jump across the list is instant so the view is right at once.
const scrollList = (list: HTMLElement, top: number) => list.scrollTo({ top: Math.max(0, top), behavior: Math.abs(top - list.scrollTop) > list.clientHeight * 1.5 ? "auto" : "smooth" });
function reveal(el: Element | null | undefined) {
  const list = el?.closest<HTMLElement>(".cue-list"), box = el as HTMLElement | null;
  if (!list || !box) return;
  const top = box.offsetTop, bottom = top + box.offsetHeight, view = list.scrollTop;
  if (top < view + 8 || box.offsetHeight > list.clientHeight - 16) scrollList(list, top - 12);
  else if (bottom > view + list.clientHeight - 8) scrollList(list, bottom - list.clientHeight + 12);
}
const at = (ms: number) => fmtClock(ms / 1000, 2);

function highlight(text: string, q: string): ReactNode {
  if (!q) return text;
  const out: ReactNode[] = [], low = text.toLowerCase();
  let i = 0;
  for (let j = low.indexOf(q); j >= 0; j = low.indexOf(q, i)) {
    out.push(text.slice(i, j), <mark key={j}>{text.slice(j, j + q.length)}</mark>);
    i = j + q.length;
  }
  out.push(text.slice(i));
  return out;
}

export function Editor({ s, dispatch, store, ctl, srtName, txtName, saveTo, source, noSound, follow, setFollow, actions, searchRef, bannerRef, fileRef }: {
  s: EdState; dispatch: Dispatch<Action>; store: PlayheadStore; ctl: VideoControls; srtName: string; txtName: string; saveTo: string; source: "edited" | "step" | null; noSound: boolean;
  follow: boolean; setFollow: (v: boolean) => void; actions: EditorActions;
  searchRef: RefObject<HTMLInputElement | null>; bannerRef: RefObject<HTMLDivElement | null>; fileRef: RefObject<HTMLInputElement | null>;
}) {
  const nowIndex = usePlayhead(store, p => cueIndexAt(s.cues, Math.round(p.t * 1000)));
  const nowId = nowIndex >= 0 ? s.cues[nowIndex].id : null;
  const [hover, setHover] = useState<number | null>(null);
  const [menu, setMenu] = useState(false);
  const [over, setOver] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const userAt = useRef(-Infinity);
  const q = s.query.trim().toLowerCase();
  const changes = changeCount(s);
  const empty = s.cues.length === 0;
  const bad = s.cues.filter(c => problem(c)).length;

  // Keep the cue at the playhead in view, unless the user moved the list in the last 3 s or is editing.
  useEffect(() => {
    const list = listRef.current, row = list?.querySelector<HTMLElement>(`[data-id="${nowId}"]`);
    if (!follow || !list || !row || s.editing != null || performance.now() - userAt.current < 3000) return;
    const y = row.offsetTop - list.scrollTop;
    if (y < list.clientHeight * 0.12 || y + row.offsetHeight > list.clientHeight * 0.75) scrollList(list, row.offsetTop - list.clientHeight * 0.3);
  }, [nowId, follow, s.editing]);

  useEffect(() => {
    if (s.errorCue == null) return;
    reveal(listRef.current?.querySelector(`[data-id="${s.errorCue}"]`));
  }, [s.errorCue]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => { if (!(e.target as Element).closest?.(".menu-wrap")) setMenu(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("pointerdown", close);
    window.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", close); window.removeEventListener("keydown", esc); };
  }, [menu]);

  const status = !s.exists && empty && s.status === "saved" ? "none" : s.status;
  const pill = { none: "No transcript yet", saved: "Saved", dirty: "Unsaved changes", saving: "Saving…", conflict: "Changed on disk", error: "Not saved" }[status];
  const meta = status === "dirty" || status === "error" || status === "conflict"
    ? (changes ? `${num(changes)} ${changes === 1 ? "change" : "changes"}, not saved yet` : "")
    : status === "saved" && s.exists ? (s.baseMtimeMs != null || source === "edited" ? `Your edited version, in ${saveTo}` : `From the session's work folder; Save keeps yours in ${saveTo}`) : "";
  const visible = q ? s.cues.filter(c => c.id === s.editing || c.text.toLowerCase().includes(q)) : s.cues;
  const matches = q ? s.cues.filter(c => c.text.toLowerCase().includes(q)).length : 0;
  const pick = (e: React.ChangeEvent<HTMLInputElement>) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) actions.useFile(f); };

  return (
    <aside
      className={`editor card${over ? " is-over" : ""}`} aria-label="Transcript editor" data-status={s.status}
      onDragOver={e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
      onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false); }}
      onDrop={e => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) actions.useFile(f); }}
    >
      <input ref={fileRef} type="file" accept=".srt,.vtt,text/vtt,application/x-subrip" hidden onChange={pick} data-transcript-file />
      {over && <div className="drop-veil">Drop the .srt or .vtt file to load its cues</div>}
      <header className="ed-head">
        <div className="ed-row">
          <span className="eyebrow">Transcript</span>
          <span className="grow" />
          {(!empty || s.exists || s.past.length > 0) && <>
            <button type="button" className="icon-btn sm" aria-label="Undo (⌘Z)" title="Undo (⌘Z)" disabled={!s.past.length} onClick={() => dispatch({ type: "undo" })}><Icon name="undo" /></button>
            <button type="button" className="icon-btn sm" aria-label="Redo (⇧⌘Z)" title="Redo (⇧⌘Z)" disabled={!s.future.length} onClick={() => dispatch({ type: "redo" })}><Icon name="redo" /></button>
            <button type="button" className="btn primary sm" data-save onClick={actions.save} disabled={s.status === "saved" || s.status === "saving"} title={`Save the .srt and the .txt into ${saveTo} (⌘S)`}>
              {s.status === "saving" ? "Saving…" : "Save"}<kbd>⌘S</kbd>
            </button>
          </>}
          <div className="menu-wrap">
            <button type="button" className="icon-btn sm" aria-label="More transcript actions" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(m => !m)}><Icon name="more" /></button>
            {menu && (
              <div className="menu" role="menu">
                <button role="menuitem" onClick={() => { setMenu(false); actions.pickFile(); }}><Icon name="upload" />Replace from file…</button>
                <button role="menuitem" onClick={() => { setMenu(false); actions.reload(); }} disabled={!s.exists}><Icon name="refresh" />Reload from disk</button>
                <button role="menuitem" onClick={() => { setMenu(false); actions.download(); }} disabled={empty}><Icon name="download" />Download .srt</button>
              </div>
            )}
          </div>
        </div>
        <span className="ed-file" title={srtName}>{srtName}</span>
        <div className="ed-row">
          <span className={`status is-${status}`} data-state={status}><i />{pill}</span>
          {meta && <span className="ed-meta" data-changes={changes}>{meta}</span>}
        </div>
        {s.error && s.status === "error" && (
          <div className="ed-err" role="alert">
            <Icon name="alert" />
            <span>{s.error} {s.errorCue != null && <button type="button" onClick={() => dispatch({ type: "edit", id: s.errorCue })}>Show it</button>}</span>
          </div>
        )}
      </header>

      {s.status === "conflict" && (
        <div className="conflict" role="alert" tabIndex={-1} ref={bannerRef} data-conflict>
          <Icon name="alert" />
          <div>
            <strong>Changed on disk</strong>
            <p>This transcript changed on disk since you opened it. Your edits are still here. Choose which version to keep.</p>
            <div className="row">
              <button type="button" className="btn secondary sm" onClick={actions.loadTheirs}>Load the disk version</button>
              <button type="button" className="btn secondary sm" onClick={actions.keepMine}>Keep mine and save</button>
            </div>
            <button type="button" className="btn link sm dl" onClick={actions.download}><Icon name="download" />Download mine as .srt</button>
          </div>
        </div>
      )}

      {empty ? (
        <EmptyState store={store} srtName={srtName} txtName={txtName} saveTo={saveTo} noSound={noSound} exists={s.exists} onStart={actions.newCue} onPick={actions.pickFile} />
      ) : (
        <>
          <div className="ed-tools">
            <label className="search">
              <Icon name="search" />
              <input ref={searchRef} type="search" placeholder="Search the transcript" aria-label="Search the transcript" value={s.query}
                onChange={e => dispatch({ type: "query", q: e.target.value })}
                onKeyDown={e => { if (e.key === "Escape" && s.query) { e.stopPropagation(); dispatch({ type: "query", q: "" }); } }} />
              {q ? <span className="count" data-count={matches}>{num(matches)} of {num(s.cues.length)}</span> : <kbd>/</kbd>}
            </label>
            <button type="button" className="btn secondary sm" onClick={actions.newCue} title="New cue at the playhead (N)"><Icon name="plus" />New cue</button>
          </div>
          <div className="ed-sub">
            <span data-cue-count={s.cues.length}>{num(s.cues.length)} {s.cues.length === 1 ? "cue" : "cues"} · times from the start of this file{bad ? ` · ${bad} to fix` : ""}</span>
            <label className="switch">
              <input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />
              <span className="sw" aria-hidden="true" />
              <span>Keep the cue at the playhead in view</span>
            </label>
          </div>
          <div
            className="cue-list" ref={listRef} role="list" aria-label="Cues"
            onWheel={() => (userAt.current = performance.now())} onTouchMove={() => (userAt.current = performance.now())}
            onPointerDown={() => (userAt.current = performance.now())}
            onKeyDown={e => { if (/^(PageUp|PageDown|Home|End)$/.test(e.key)) userAt.current = performance.now(); }}
          >
            {q && matches === 0 && <p className="no-match">No cue contains “{s.query}”.</p>}
            {visible.map(c => {
              const i = s.cues.indexOf(c), next = s.cues[i + 1];
              const why = problem(c), over = overlapMs(s.cues, i);
              return c.id === s.editing ? (
                <EditCard key={c.id} c={c} n={i + 1} count={s.cues.length} hasNext={!!next} bad={why} overlap={over} isNow={c.id === nowId}
                  store={store} dispatch={dispatch} insertAfter={actions.insertAfter} />
              ) : (
                <CueRow key={c.id} c={c} n={i + 1} now={c.id === nowId} changed={isChanged(s, c)} bad={why} overlap={over} q={q}
                  hover={hover === c.id} onHover={setHover} dispatch={dispatch} ctl={ctl}>
                  {hover === c.id && <HoverActions c={c} hasNext={!!next} store={store} dispatch={dispatch} insertAfter={actions.insertAfter} />}
                </CueRow>
              );
            })}
          </div>
        </>
      )}
    </aside>
  );
}

const CueRow = memo(function CueRow({ c, n, now, changed, bad, overlap, q, hover, onHover, dispatch, ctl, children }: {
  c: EdCue; n: number; now: boolean; changed: boolean; bad: string | null; overlap: number; q: string; hover: boolean;
  onHover: (id: number) => void; dispatch: Dispatch<Action>; ctl: VideoControls; children?: ReactNode;
}) {
  const cls = `cue${now ? " is-now" : ""}${changed ? " is-chg" : ""}${bad ? " is-bad" : ""}${overlap ? " is-overlap" : ""}${hover ? " is-hover" : ""}`;
  return (
    <div className={cls} data-id={c.id} data-n={n} data-end={at(c.endMs)} role="listitem" onMouseEnter={() => onHover(c.id)} onFocus={() => onHover(c.id)}>
      <button type="button" className="cue-t" onClick={() => ctl.seek(c.startMs / 1000)} title={`${at(c.startMs)} to ${at(c.endMs)}: click to go there`} aria-label={`Cue ${n}: go to ${at(c.startMs)}`}>
        <span>{at(c.startMs)}</span>
      </button>
      <button type="button" className="cue-x" onClick={() => dispatch({ type: "edit", id: c.id })} title="Click to edit">
        {c.text ? <Spoken text={c.text} mark={t => highlight(t, q)} /> : <span className="empty-text">No text yet</span>}
      </button>
      {bad && <div className="cue-note bad">{bad}</div>}
      {overlap > 0 && <div className="cue-note warn">Overlaps the next cue by {secs(overlap)} s.</div>}
      {children}
    </div>
  );
});

function HoverActions({ c, hasNext, store, dispatch, insertAfter }: { c: EdCue; hasNext: boolean; store: PlayheadStore; dispatch: Dispatch<Action>; insertAfter: (id: number) => void }) {
  const t = usePlayhead(store, p => Math.round(p.t * 1000));
  const inside = t > c.startMs && t < c.endMs;
  const b = (label: string, icon: string, onClick: () => void, disabled = false, why = "") => (
    <button type="button" className="icon-btn" aria-label={label} title={disabled && why ? `${label}: ${why}` : label} disabled={disabled} onClick={onClick}><Icon name={icon} /></button>
  );
  return (
    <div className="cue-acts" role="toolbar" aria-label="Cue actions">
      {b(`Set start to the playhead, ${at(t)}`, "start", () => dispatch({ type: "times", id: c.id, startMs: t }), t >= c.endMs, "the playhead is after the end of this cue")}
      {b(`Set end to the playhead, ${at(t)}`, "end", () => dispatch({ type: "times", id: c.id, endMs: t }), t <= c.startMs, "the playhead is before the start of this cue")}
      {b("Split at the playhead", "cut", () => dispatch({ type: "split", id: c.id, atMs: t }), !inside, "the playhead is outside this cue")}
      {b("Merge with the next cue", "merge", () => dispatch({ type: "merge", id: c.id }), !hasNext, "this is the last cue")}
      {b("Insert a cue after this one", "plus", () => insertAfter(c.id))}
      {b("Delete this cue", "trash", () => dispatch({ type: "delete", id: c.id }))}
    </div>
  );
}

function TimeField({ id, ms, label, onCommit }: { id: string; ms: number; label: string; onCommit: (ms: number) => void }) {
  const [draft, setDraftState] = useState<string | null>(null);
  const draftRef = useRef<string | null>(null); // read on blur, which can come before the state update
  const setDraft = (v: string | null) => { draftRef.current = v; setDraftState(v); };
  const [badInput, setBad] = useState(false);
  const commit = () => {
    const d = draftRef.current;
    if (d == null) return;
    const v = parseClock(d);
    setDraft(null);
    if (v == null || v < 0) { setBad(true); setTimeout(() => setBad(false), 1200); return; }
    if (Math.round(v * 1000) !== ms) onCommit(Math.round(v * 1000));
  };
  return (
    <input id={id} className={`time-in${badInput ? " bad" : ""}`} value={draft ?? at(ms)} aria-label={label} spellCheck={false} inputMode="decimal"
      onFocus={e => e.currentTarget.select()} onChange={e => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); commit(); } if (e.key === "Escape") { e.stopPropagation(); setDraft(null); e.currentTarget.blur(); } }} />
  );
}

function EditCard({ c, n, count, hasNext, bad, overlap, isNow, store, dispatch, insertAfter }: {
  c: EdCue; n: number; count: number; hasNext: boolean; bad: string | null; overlap: number; isNow: boolean;
  store: PlayheadStore; dispatch: Dispatch<Action>; insertAfter: (id: number) => void;
}) {
  const t = usePlayhead(store, p => Math.round(p.t * 1000));
  const area = useRef<HTMLTextAreaElement>(null);
  const inside = t > c.startMs && t < c.endMs;
  useLayoutEffect(() => {
    const el = area.current;
    if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight + 2}px`; }
  }, [c.text]);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    el.setSelectionRange(el.value.length, el.value.length);
    // After a frame, so it replaces any smooth "keep in view" scroll still running.
    const id = requestAnimationFrame(() => reveal(el.closest(".ecard")));
    return () => cancelAnimationFrame(id);
  }, [c.id]);
  const why = inside ? "The playhead is inside this cue." : t >= c.endMs ? `The playhead is ${secs(t - c.endMs)} s after this cue.` : `The playhead is ${secs(c.startMs - t)} s before this cue.`;
  const caret = () => { const el = area.current; return el && el.selectionStart === el.selectionEnd ? el.selectionStart : null; };
  return (
    <div className={`ecard${bad ? " is-bad" : ""}`} data-id={c.id} data-editing role="listitem" aria-label={`Editing cue ${n}`}>
      <div className="ec-top">
        <span><b>Cue {n}</b> of {num(count)} · {secs(c.endMs - c.startMs)} s</span>
        <span className="ec-badge">Editing</span>
        {isNow && <span className="ec-badge now">Now</span>}
        <span className="grow" />
        <button type="button" className="chip-btn danger" onClick={() => dispatch({ type: "delete", id: c.id })}><Icon name="trash" />Delete</button>
      </div>
      <div className="ec-times">
        <label htmlFor={`cs${c.id}`}>Start</label>
        <TimeField id={`cs${c.id}`} ms={c.startMs} label="Start time" onCommit={ms => dispatch({ type: "times", id: c.id, startMs: ms })} />
        <button type="button" className="chip-btn" disabled={t >= c.endMs} onClick={() => dispatch({ type: "times", id: c.id, startMs: t })}><Icon name="start" />Set start <span className="t">{at(t)}</span></button>
        <label htmlFor={`ce${c.id}`}>End</label>
        <TimeField id={`ce${c.id}`} ms={c.endMs} label="End time" onCommit={ms => dispatch({ type: "times", id: c.id, endMs: ms })} />
        <button type="button" className="chip-btn" disabled={t <= c.startMs} onClick={() => dispatch({ type: "times", id: c.id, endMs: t })}><Icon name="end" />Set end <span className="t">{at(t)}</span></button>
      </div>
      <textarea ref={area} className="ec-text" rows={2} value={c.text} placeholder="Type what is said" aria-label={`Text of cue ${n}`}
        onChange={e => dispatch({ type: "text", id: c.id, text: e.target.value })}
        onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); dispatch({ type: "next" }); }
          else if (e.key === "Escape") { e.preventDefault(); dispatch({ type: "cancel" }); }
        }} />
      <div className="ec-acts">
        <button type="button" className="chip-btn" disabled={!inside} onClick={() => dispatch({ type: "split", id: c.id, atMs: t, caret: caret() })}><Icon name="cut" />Split at playhead</button>
        <button type="button" className="chip-btn" disabled={!hasNext} onClick={() => dispatch({ type: "merge", id: c.id })}><Icon name="merge" />Merge with next</button>
        <button type="button" className="chip-btn" onClick={() => insertAfter(c.id)}><Icon name="plus" />Insert cue after</button>
      </div>
      {bad && <div className="ec-msg bad"><Icon name="alert" />{bad} It must be fixed before saving.</div>}
      {overlap > 0 && <div className="ec-msg warn"><Icon name="info" />It overlaps the next cue by {secs(overlap)} s. That is allowed.</div>}
      <div className="ec-why"><span data-why>{why}</span><span><kbd>Return</kbd> next <kbd>Esc</kbd> undo typing</span></div>
    </div>
  );
}

function EmptyState({ store, srtName, txtName, saveTo, noSound, exists, onStart, onPick }: {
  store: PlayheadStore; srtName: string; txtName: string; saveTo: string; noSound: boolean; exists: boolean; onStart: () => void; onPick: () => void;
}) {
  const t = usePlayhead(store, p => Math.round(p.t * 100) / 100);
  return (
    <div className="empty" data-empty>
      <div className="empty-in">
        <div className="ghost-page" aria-hidden="true"><div className="ghost-row"><span className="t">{fmtClock(t, 2)}</span><span className="x">First cue starts at the playhead</span></div></div>
        <h2>{exists ? "The transcript is empty" : "No transcript yet"}</h2>
        <p className="lead">{noSound ? "This camera records no sound, so there is usually nothing to transcribe. You can still add notes as cues."
          : exists ? `${srtName} has no cues. Add a cue where you hear speech, or bring in an .srt or .vtt file.`
          : "This video has no transcript yet. Start one where the video is, or bring in an .srt or .vtt file you already have."}</p>
        <div className="empty-acts">
          <button type="button" className="big-btn primary" onClick={onStart} data-start>
            <Icon name="plus" /><span><b>Start a new transcript</b><span className="sub">First cue at the playhead, <span className="mono">{fmtClock(t, 2)}</span></span></span><kbd>N</kbd>
          </button>
          <button type="button" className="big-btn" onClick={onPick}>
            <Icon name="upload" /><span><b>Load an .srt or .vtt file</b><span className="sub">Times are read from the start of this video</span></span>
          </button>
        </div>
        <div className="drop">or drop an .srt file here</div>
        <p className="empty-note">Saving writes <span className="mono">{srtName}</span> and <span className="mono">{txtName}</span> into the session&apos;s <span className="mono">{saveTo}</span>; the video is never changed.</p>
      </div>
    </div>
  );
}
