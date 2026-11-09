"use client";
// One video and its transcript. The reducer in transcript.ts owns the cues; the playhead store owns the video position.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { cueIndexAt, formatSrt, normalizeCues, parseSrt, type Cue } from "@/lib/srt";
import { num } from "@/lib/format";
import { Icon } from "../Icon";
import { NavLink, confirmLeave, isTyping, setUnsaved } from "../nav";
import { LAST_VIDEO_KEY } from "../TopBar";
import { changeCount, init, isChanged, problem, reducer, sameAsSaved } from "./transcript";
import { Editor, type EditorActions } from "./Editor";
import { Player } from "./Player";
import { createPlayhead, type VideoControls } from "./playhead";
import { Timeline } from "./Timeline";

export type SingleProps = {
  videoPath: string; // <session>/<camera>/<part>
  src: string;
  srtName: string;
  txtName: string;
  transcript: { cues: Cue[]; mtimeMs: number | null; exists: boolean; skipped: number };
  source: "edited" | "step" | null; // whose transcript opened: the person's edited one, the one in the session's work folder, or none yet
  saveTo: string; // the session folder that edits are saved into, as shown to the person
  title: string;
  tag: string | null;
  sub: string[];
  fps: number;
  noSound: boolean;
  clock: { from: number; rate: number; multi: string } | null; // session clock of this file: clock = from + t * rate
  pager: { prev: { href: string; label: string } | null; next: { href: string; label: string } | null; text: string } | null;
  startT: number | null;
  openEdit: boolean;
  nav: string; // new on every navigation to this page, the same again on Back or Forward
  back: { href: string; label: string } | null;
};

export function SingleView(p: SingleProps) {
  const [s, dispatch] = useReducer(reducer, p.transcript, t => init(t.cues, t.mtimeMs, t.exists));
  const stateRef = useRef(s);
  stateRef.current = s;
  const [store] = useState(() => createPlayhead(p.startT ?? 0));
  const videoRef = useRef<HTMLVideoElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const bannerRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const startRef = useRef(p.startT);
  const [captions, setCaptions] = useState(p.transcript.cues.length > 0);
  const [follow, setFollow] = useState(true);
  const [toast, setToast] = useState<{ text: string; n: number } | null>(null);
  const router = useRouter();
  const say = useCallback((text: string) => setToast(t => ({ text, n: (t?.n ?? 0) + 1 })), []);
  const fileMs = () => { const d = store.get().duration; return d > 0 && Number.isFinite(d) ? Math.round(d * 1000) : 1e12; };
  const nowMs = () => Math.round(store.get().t * 1000);

  const ctl = useMemo<VideoControls>(() => {
    const seek = (t: number) => {
      const el = videoRef.current;
      if (!el) return;
      const d = el.duration > 0 && Number.isFinite(el.duration) ? el.duration : Infinity;
      const x = Math.max(0, Math.min(d, t));
      el.currentTime = x;
      store.set({ t: x });
    };
    return {
      seek,
      toggle: () => { const el = videoRef.current; if (el) { if (el.paused) el.play().catch(() => {}); else el.pause(); } },
      nudge: dt => seek(store.get().t + dt),
      step: n => seek((Math.floor(store.get().t * p.fps + 1e-6) + n + 0.5) / p.fps),
    };
  }, [store, p.fps]);

  // The video drives the playhead: every frame while playing, on seeks and when the metadata arrives.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let raf = 0;
    const loop = () => { store.set({ t: v.currentTime }); raf = requestAnimationFrame(loop); };
    const play = () => { store.set({ playing: true }); cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); };
    const stop = () => { cancelAnimationFrame(raf); store.set({ playing: false, t: v.currentTime }); };
    const moved = () => { if (v.paused) store.set({ t: v.currentTime }); };
    const meta = () => {
      store.set({ duration: v.duration });
      if (startRef.current != null) { v.currentTime = startRef.current; startRef.current = null; }
    };
    const on: [string, () => void][] = [["play", play], ["pause", stop], ["ended", stop], ["seeked", moved], ["timeupdate", moved], ["loadedmetadata", meta], ["durationchange", meta]];
    on.forEach(([e, f]) => v.addEventListener(e, f));
    if (v.readyState >= 1) meta();
    return () => { cancelAnimationFrame(raf); v.pause(); on.forEach(([e, f]) => v.removeEventListener(e, f)); };
  }, [store]);

  // Effects run again when Back shows a page the router kept hidden; what must happen once is guarded.
  const [ready, setReady] = useState(false);
  const told = useRef(false);
  useEffect(() => {
    setReady(true);
    try { localStorage.setItem(LAST_VIDEO_KEY, location.pathname); } catch {}
    if (p.transcript.skipped && !told.current) say(`${p.transcript.skipped} ${p.transcript.skipped === 1 ? "block" : "blocks"} of ${p.srtName} could not be read and are left out.`);
    told.current = true;
  }, []);

  // ?t= (and ?edit=1) apply once per navigation, so the same link works twice, and Back keeps the place it left.
  const applied = useRef<string | null>(null);
  useEffect(() => {
    if (p.startT == null || applied.current === p.nav) return;
    applied.current = p.nav;
    if (videoRef.current && videoRef.current.readyState >= 1) ctl.seek(p.startT); else startRef.current = p.startT;
    store.set({ t: p.startT });
    if (p.openEdit) {
      const i = cueIndexAt(stateRef.current.cues, Math.round(p.startT * 1000));
      if (i >= 0) dispatch({ type: "edit", id: stateRef.current.cues[i].id });
    }
  }, [p.nav, p.openEdit, p.startT, ctl, store]);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 7000);
    return () => clearTimeout(id);
  }, [toast]);

  const dirty = changeCount(s) > 0;
  useEffect(() => {
    setUnsaved(dirty);
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      setUnsaved(false);
      // Hidden by Back or Forward, which ask nothing, or dropped by the router later: the edits go to history first.
      if (changeCount(stateRef.current) > 0) stashEdits().catch(() => {});
    };
  }, [dirty]);

  const put = (body: object) => fetch("/api/transcript", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video: p.videoPath, ...body }) })
    .then(async r => ({ ok: r.ok, status: r.status, j: (await r.json().catch(() => ({}))) as Record<string, unknown> }));
  const stashEdits = () => put({ cues: stateRef.current.cues.map(({ startMs, endMs, text }) => ({ startMs, endMs, text })), stash: true });

  const save = async (force = false) => {
    const st = stateRef.current;
    if (st.status === "saving") return;
    if (st.status === "conflict" && !force) { bannerRef.current?.focus(); return; }
    if (st.status === "saved" && !force) return;
    const broken = st.cues.find(c => problem(c));
    if (broken) {
      dispatch({ type: "failed", error: `Cue ${st.cues.indexOf(broken) + 1}: ${problem(broken)} Fix it, then save.`, cueId: broken.id });
      return;
    }
    const norm = normalizeCues(st.cues);
    if ("error" in norm) { dispatch({ type: "failed", error: norm.error }); return; }
    const cues = norm.cues.map((c, i) => ({ ...c, id: st.cues[i].id }));
    dispatch({ type: "saving" });
    try {
      let r = await put({ cues: norm.cues, baseMtimeMs: st.baseMtimeMs, force });
      if (r.status === 409) {
        // Only changed words are a conflict: a rewrite with the same words is saved over, still only if untouched since.
        const disk = await fetch(`/api/transcript?video=${encodeURIComponent(p.videoPath)}`, { cache: "no-store" }).then(x => x.json()).catch(() => null);
        if (Array.isArray(disk?.cues) && sameAsSaved(st, disk.cues)) r = await put({ cues: norm.cues, baseMtimeMs: disk.mtimeMs });
      }
      if (r.ok) {
        dispatch({ type: "saved", cues, mtimeMs: r.j.mtimeMs as number });
        say(force ? `Saved yours as ${p.srtName} and .txt; the version that was on disk is kept in history.`
          : `Saved ${p.srtName} and .txt${r.j.backup ? "; the previous version is kept in history" : ""}.`);
      } else if (r.status === 409) {
        dispatch({ type: "conflict", mtimeMs: (r.j.mtimeMs as number | null) ?? null });
        setToast(null);
        requestAnimationFrame(() => bannerRef.current?.focus());
      } else {
        const line = /Line (\d+)/.exec(String(r.j.error ?? ""));
        dispatch({ type: "failed", error: String(r.j.error ?? `Saving failed (${r.status}).`), cueId: line ? cues[+line[1] - 1]?.id ?? null : null });
      }
    } catch {
      dispatch({ type: "failed", error: "Saving failed: the app's server did not answer." });
    }
  };

  const loadDisk = async (ask: boolean) => {
    const st = stateRef.current, mine = changeCount(st) > 0;
    if (ask && mine && !window.confirm("Reload the transcript from disk? Your unsaved changes will be replaced. A copy of them is kept in .transcript_history first.")) return;
    let kept = "";
    if (mine) {
      const r = await stashEdits();
      if (!r.ok) { say(`Could not keep a copy of your edits (${r.j.error ?? r.status}), so nothing was reloaded.`); return; }
      kept = ` Your unsaved edits were kept in ${r.j.backup}; Undo also brings them back.`;
    }
    const r = await fetch(`/api/transcript?video=${encodeURIComponent(p.videoPath)}`, { cache: "no-store" });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j) { say(`Could not read the transcript from disk (${j?.error ?? r.status}).`); return; }
    dispatch({ type: "load", cues: j.cues, mtimeMs: j.mtimeMs, exists: j.exists, keepHistory: true });
    say(`Loaded the version on disk.${kept}`);
  };

  // On every show (Back and Forward re-show this page from the router's cache) and when the window gets focus again,
  // look at the disk: another program may have rewritten the file. An unedited transcript takes the new version;
  // edits get the conflict banner only when the words on disk differ from the ones they started from.
  useEffect(() => {
    const look = async () => {
      const before = stateRef.current.baseMtimeMs;
      const j = await fetch(`/api/transcript?video=${encodeURIComponent(p.videoPath)}`, { cache: "no-store" }).then(r => (r.ok ? r.json() : null)).catch(() => null);
      const st = stateRef.current;
      if (!j || !Array.isArray(j.cues) || j.mtimeMs === st.baseMtimeMs || st.baseMtimeMs !== before || st.status === "saving" || st.status === "conflict") return;
      if (sameAsSaved(st, j.cues)) dispatch({ type: "rebase", mtimeMs: j.mtimeMs, exists: j.exists });
      else if (changeCount(st) === 0) {
        dispatch({ type: "load", cues: j.cues, mtimeMs: j.mtimeMs, exists: j.exists, keepHistory: true });
        say(`${p.srtName} changed on disk; this is the new version. Undo shows the one before.`);
      } else dispatch({ type: "conflict", mtimeMs: j.mtimeMs });
    };
    look();
    window.addEventListener("focus", look);
    return () => window.removeEventListener("focus", look);
  }, [p.videoPath, p.srtName, say]);

  const actions: EditorActions = {
    save: () => save(),
    reload: () => loadDisk(true),
    loadTheirs: () => loadDisk(false),
    keepMine: () => save(true),
    download: () => {
      const st = stateRef.current, a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([formatSrt(st.cues)], { type: "text/plain" }));
      a.download = p.srtName;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    },
    pickFile: () => fileRef.current?.click(),
    useFile: async f => {
      const { cues, skipped } = parseSrt(await f.text());
      if (!cues.length) { say(`No cues could be read from ${f.name}.`); return; }
      dispatch({ type: "replace", cues });
      say(`Loaded ${num(cues.length)} cues from ${f.name}, not saved yet${skipped ? `; ${skipped} ${skipped === 1 ? "block" : "blocks"} could not be read` : ""}. Undo brings the previous cues back.`);
    },
    newCue: () => dispatch({ type: "newAt", atMs: nowMs(), fileMs: fileMs() }),
    insertAfter: id => dispatch({ type: "insertAfter", id, atMs: nowMs(), fileMs: fileMs() }),
  };
  const actionsRef = useRef(actions);
  actionsRef.current = actions;

  useEffect(() => {
    let spaceDown = false;
    const down = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey, typing = isTyping(e.target);
      if (mod && !e.altKey && e.code === "KeyS") {
        e.preventDefault();
        // A time typed into a Start or End field counts once the field lets go of it.
        const field = document.activeElement as HTMLElement | null;
        if (field?.classList.contains("time-in")) { field.blur(); setTimeout(() => actionsRef.current.save()); } else actionsRef.current.save();
        return;
      }
      if (mod && !e.altKey && (e.code === "KeyZ" || e.code === "KeyY")) {
        if (typing && (e.target as HTMLElement).closest(".search, .time-in, .cloud-in")) return; // these fields keep their own undo
        e.preventDefault();
        dispatch({ type: e.shiftKey || e.code === "KeyY" ? "redo" : "undo" });
        return;
      }
      if (typing) {
        if (e.altKey && !mod && ["KeyK", "KeyJ", "KeyL"].includes(e.code) && (e.target as HTMLElement).closest(".ecard")) {
          e.preventDefault();
          if (e.code === "KeyK") ctl.toggle(); else ctl.nudge(e.code === "KeyJ" ? -5 : 5);
        }
        return;
      }
      if (mod || e.altKey) return;
      // Esc there closes the shortcut sheet or the menu; the cue being edited stays open.
      if (e.code === "Escape" && (e.target as Element).closest?.("dialog[open], .menu-wrap")) return;
      switch (e.code) {
        case "Space": case "KeyK": e.preventDefault(); if (!e.repeat) ctl.toggle(); spaceDown = e.code === "Space"; break;
        case "KeyJ": ctl.nudge(-5); break;
        case "KeyL": ctl.nudge(5); break;
        case "ArrowLeft": e.preventDefault(); if (e.shiftKey) ctl.nudge(-1); else ctl.step(-1); break;
        case "ArrowRight": e.preventDefault(); if (e.shiftKey) ctl.nudge(1); else ctl.step(1); break;
        case "KeyN": e.preventDefault(); actionsRef.current.newCue(); break;
        case "KeyC": setCaptions(c => !c); break;
        case "Slash": if (!e.shiftKey) { e.preventDefault(); searchRef.current?.focus(); } break;
        case "Escape": if (stateRef.current.editing != null) dispatch({ type: "edit", id: null }); break;
      }
    };
    // A focused button would also "click" on the Space keyup.
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && spaceDown) { e.preventDefault(); spaceDown = false; } };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [ctl, store]);

  const clockAt = p.clock ? (t: number) => p.clock!.from + t * p.clock!.rate : null;
  const openMulti = () => {
    if (!p.clock || !clockAt) return;
    const href = `${p.clock.multi}?c=${clockAt(Math.floor(store.get().t * p.fps + 1e-6) / p.fps).toFixed(4)}`; // the shown frame's own moment
    if (confirmLeave()) router.push(href);
  };

  return (
    <div className="single" data-screen="single" data-ready={ready || undefined}>
      <header className="sv-head">
        <div>
          <h1>{p.title}{p.tag && <span className="tag">{p.tag}</span>}</h1>
          <div className="sv-sub">
            {p.sub.map((x, i) => <span key={i} style={{ display: "contents" }}>{i > 0 && <span className="dot" />}<span className={i === 0 ? "mono" : undefined}>{x}</span></span>)}
          </div>
        </div>
        <div className="sv-actions">
          {p.back && <NavLink className="btn ghost" href={p.back.href}><Icon name="arrowL" />{p.back.label}</NavLink>}
          {p.pager && (
            <div className="pager">
              {p.pager.prev ? <NavLink className="icon-btn" href={p.pager.prev.href} aria-label={`Previous: ${p.pager.prev.label}`} title={p.pager.prev.label}><Icon name="chevL" /></NavLink>
                : <span className="icon-btn" aria-disabled="true" style={{ opacity: .4 }}><Icon name="chevL" /></span>}
              <span className="num">{p.pager.text}</span>
              {p.pager.next ? <NavLink className="icon-btn" href={p.pager.next.href} aria-label={`Next: ${p.pager.next.label}`} title={p.pager.next.label}><Icon name="chevR" /></NavLink>
                : <span className="icon-btn" aria-disabled="true" style={{ opacity: .4 }}><Icon name="chevR" /></span>}
            </div>
          )}
          {p.clock && <button type="button" className="btn secondary" onClick={openMulti} data-open-multi><Icon name="views" />Open this moment in multi-view</button>}
        </div>
      </header>
      <div className="single-grid">
        <Player src={p.src} videoRef={videoRef} boxRef={boxRef} store={store} ctl={ctl} cues={s.cues} captions={captions} setCaptions={setCaptions} fps={p.fps} clockAt={clockAt}
          timeline={<Timeline cues={s.cues} editing={s.editing} changed={c => isChanged(s, c)} bad={c => !!problem(c)} store={store} ctl={ctl}
            onOpen={id => { const c = s.cues.find(x => x.id === id); if (c) ctl.seek(c.startMs / 1000); dispatch({ type: "edit", id }); }} />} />
        <Editor s={s} dispatch={dispatch} store={store} ctl={ctl} srtName={p.srtName} txtName={p.txtName} saveTo={p.saveTo} source={p.source} noSound={p.noSound} follow={follow} setFollow={setFollow}
          actions={actions} searchRef={searchRef} bannerRef={bannerRef} fileRef={fileRef} />
      </div>
      {toast && (
        <div className="toast" role="status" key={toast.n} data-toast>
          <Icon name="check" /><span>{toast.text}</span>
          <button type="button" aria-label="Close" onClick={() => setToast(null)}><Icon name="x" /></button>
        </div>
      )}
    </div>
  );
}
