"use client";
// All cameras on the session clock: the parts to check and the hand move, panels, transport and scrubber, and the source
// camera's transcript.
import { memo, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { cueIndexAt, fmtClock, parseClock, type Cue } from "@/lib/srt";
import { camName, mediaUrl, num, waitText, watchHref } from "@/lib/format";
import { flagMarks, partAt, period, shortLabel, timeOfClock, timeOfFrame, type Camera, type Part, type Session } from "@/lib/session";
import type { RunState } from "@/server/run";
import { Icon } from "../Icon";
import { useKey } from "../keys";
import { Spoken } from "../Spoken";
import { NavLink, isTyping } from "../nav";
import { HandMove } from "./HandMove";
import { OneClock } from "./oneClock";

declare global { interface Window { __oneClock?: OneClock } }

type Transcript = { state: "loading" | "ready" | "none" | "error"; cues: Cue[] };
const noop = () => () => {};

export function MultiView({ job, session: s, density, cueCounts, initialClock, initialPart, nav, run }: {
  job: string; session: Session; density: Record<string, number[]>; cueCounts: Record<string, number | null>; // cueCounts: by "cam/part"
  initialPart: string | null; // ?part=, a flagged part to open the hand move on
  run: RunState;
  initialClock: number | null; // ?c=, when the address names a moment
  nav: string; // new on every navigation to this page, the same again on Back or Forward
}) {
  const start = initialClock ?? Math.max(s.clock.min, 0);
  const videos = useRef(new Map<string, HTMLVideoElement>());
  const kept = useRef<OneClock | null>(null);
  const [ctl, setCtl] = useState<OneClock | null>(null);
  const cache = useRef(new Map<string, Transcript>());
  const [gen, setGen] = useState(0);
  useSyncExternalStore(ctl?.subscribe ?? noop, () => ctl?.version ?? -1, () => -1);
  const sound = s.cameras.filter(c => c.sound);
  const [source, setSource] = useState(sound[0]?.key ?? "");
  const [stream, setStream] = useState<"colour" | "depth">("colour");
  const [stepKind, setStepKind] = useState<"depth" | "60">("depth");
  const [focus, setFocus] = useState<string | null>(null);
  const [captions, setCaptions] = useState(true);
  const key = useKey();
  const depthCam = s.cameras.find(c => c.kind === "depth" && c.parts.length);
  const stepS = stepKind === "depth" && depthCam ? period(depthCam, depthCam.parts[0]) : 1 / 60;

  const sourceRef = useRef(source);
  sourceRef.current = source;
  // One controller for the life of the page. The router hides a page instead of unmounting it, and runs this again
  // when Back shows it: the clock, the depth picture and the source then carry on where they were. A new session from
  // the server (a clock rerun) gets a new controller at the same moment.
  useEffect(() => {
    if (kept.current && kept.current.s !== s) kept.current = new OneClock(s, videos.current, f => mediaUrl(job, f), kept.current.clock);
    const c = (kept.current ??= new OneClock(s, videos.current, f => mediaUrl(job, f), start));
    c.attach();
    c.setSource(sourceRef.current);
    c.showClock(c.clock);
    setCtl(c);
    cache.current.clear(); // transcripts may have been edited in the single view meanwhile
    setGen(g => g + 1);
    window.__oneClock = c;
    return () => { c.detach(); if (window.__oneClock === c) delete window.__oneClock; };
  }, [s, job]);

  // ?c= applies once per navigation that has one, also when it repeats the last one; a link without it (the tab)
  // and Back keep the clock where it was.
  const applied = useRef(nav);
  useEffect(() => {
    if (!ctl || initialClock == null || nav === applied.current) return;
    applied.current = nav;
    ctl.showClock(initialClock);
  }, [ctl, nav, initialClock]);
  useEffect(() => { ctl?.setSource(source); }, [ctl, source]);

  const clock = ctl ? ctl.shownClock() : start, playing = !!ctl?.playing;

  // The address follows the clock to 0.1 ms while paused, so Back and refresh return to the same moment.
  const settled = ctl && !playing ? Math.round(ctl.clock * 1e4) : null;
  useEffect(() => {
    if (settled == null) return;
    const id = setTimeout(() => {
      // Back pressed during the wait has already moved the address to the page it went to.
      if (location.pathname.endsWith("/multiview")) window.history.replaceState(null, "", `?c=${(settled / 1e4).toFixed(4)}`);
    }, 300);
    return () => clearTimeout(id);
  }, [settled]);

  useEffect(() => {
    if (!ctl) return;
    const down = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.code === "Space") { e.preventDefault(); if (!e.repeat) ctl.togglePlay(); }
      else if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
        e.preventDefault();
        ctl.showClock(ctl.shownClock() + (e.code === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : stepS));
      }
    };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && !isTyping(e.target)) e.preventDefault(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [ctl, stepS]);

  const srcCam = s.cameras.find(c => c.key === source);
  const srcPart = srcCam ? partAt(srcCam, clock) ?? null : null;
  const srcKey = srcCam && srcPart ? `${srcCam.key}/${srcPart.key}` : null;
  const transcript = useTranscript(job, srcKey, srcKey ? cueCounts[srcKey] != null : false, cache.current, gen);
  const fileMs = srcCam && srcPart ? Math.round(timeOfClock(srcCam, srcPart, clock) * 1000) : -1;
  const nowCue = transcript.state === "ready" ? cueIndexAt(transcript.cues, fileMs) : -1;

  return (
    <div className="multi" data-screen="multi">
        <HandMove job={job} s={s} ctl={ctl} initialPart={initialPart} run={run} />
        <section className={`panels${focus ? " is-focus" : ""}`} aria-label="Cameras">
          {s.cameras.map(cam => (
            <CameraPanel key={cam.key} cam={cam} manual={s.manual} job={job} ctl={ctl} clock={clock} source={source} stream={stream} focus={focus}
              setFocus={setFocus} setStream={v => { setStream(v); ctl?.setStream(v); }}
              register={el => { if (el) videos.current.set(cam.key, el); }}
              caption={captions && cam.key === source && nowCue >= 0 ? transcript.cues[nowCue].text : null} />
          ))}
        </section>

        <section className="card transport-m" aria-label="Session clock">
          <div className="tp-row">
            <button type="button" className="btn primary" onClick={() => ctl?.togglePlay()} data-play-all disabled={!ctl}>
              <Icon name={playing ? "pause" : "play"} />{playing ? "Pause" : "Play all"}
            </button>
            <div className="clock-ctl">
              <button type="button" className="icon-btn" aria-label="One step back (←)" title="One step back (←)" onClick={() => ctl?.showClock(ctl.shownClock() - stepS)} data-step-back><Icon name="fback" /></button>
              <ClockField clock={clock} min={s.clock.min} max={s.clock.max} onGo={c => ctl?.showClock(c)} />
              <button type="button" className="icon-btn" aria-label="One step forward (→)" title="One step forward (→)" onClick={() => ctl?.showClock(ctl.shownClock() + stepS)} data-step-fwd><Icon name="ffwd" /></button>
            </div>
            <div className="seg" role="group" aria-label="Step size of the frame buttons">
              <button type="button" aria-pressed={stepKind === "60"} onClick={() => setStepKind("60")}>1/60 s</button>
              {depthCam && <button type="button" aria-pressed={stepKind === "depth"} onClick={() => setStepKind("depth")}>Depth camera frame</button>}
            </div>
            <span className="grow" />
            <span className="hint">Type a time, then <kbd>{key("return")}</kbd> · Click the bar to jump</span>
            <div className="legend" aria-hidden="true">
              <span><i style={{ width: 3, background: "var(--warn-dot)" }} />Flagged part</span>
              <span><i style={{ background: "var(--mark)", opacity: 0.55 }} />Cues per minute</span>
              <span><i style={{ background: "var(--clip)", border: "1px solid var(--clip-line)" }} />Recording</span>
            </div>
          </div>
          <Scrubber s={s} clock={clock} source={source} density={density[source] ?? []} onGo={c => ctl?.showClock(c)} />
        </section>

        <Strip job={job} cam={srcCam ?? null} part={srcPart} sound={sound} source={source} setSource={setSource} clock={clock} transcript={transcript}
          nowCue={nowCue} fileMs={fileMs} captions={captions} setCaptions={setCaptions} onGo={c => ctl?.showClock(c)} />
    </div>
  );
}

function ClockField({ clock, min, max, onGo }: { clock: number; min: number; max: number; onGo: (c: number) => void }) {
  const key = useKey();
  const [draft, setDraftState] = useState<string | null>(null);
  const draftRef = useRef<string | null>(null); // read on blur, which can come before the state update
  const edited = useRef(false); // a click in and out without typing must not move the clock
  const setDraft = (v: string | null) => { draftRef.current = v; setDraftState(v); };
  const [bad, setBad] = useState(false);
  const go = () => {
    const d = draftRef.current;
    setDraft(null);
    if (d == null || !edited.current) return;
    const c = parseClock(d);
    if (c == null || c < min - 600 || c > max + 600) { setBad(true); setTimeout(() => setBad(false), 1200); return; }
    onGo(c);
  };
  return (
    <input className={`clock-field${bad ? " bad" : ""}`} aria-label="Session clock, minutes:seconds" title={`Type a time such as 45:00, 45:00.25 or −1:30, then press ${key("return")}`}
      value={draft ?? fmtClock(clock, 2)} spellCheck={false} autoComplete="off" data-clock-field
      onFocus={e => { edited.current = false; setDraft(e.currentTarget.value); requestAnimationFrame(() => e.target.select()); }}
      onChange={e => { edited.current = true; setDraft(e.target.value); }} onBlur={go}
      onKeyDown={e => {
        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
        if (e.key === "Escape") { setDraft(null); e.currentTarget.blur(); }
      }} />
  );
}

function CameraPanel({ cam, manual, job, ctl, clock, source, stream, focus, setFocus, setStream, register, caption }: {
  cam: Camera; manual: Session["manual"]; job: string; ctl: OneClock | null; clock: number; source: string; stream: "colour" | "depth"; focus: string | null;
  setFocus: (f: (v: string | null) => string | null) => void; setStream: (v: "colour" | "depth") => void; register: (el: HTMLVideoElement | null) => void; caption: string | null;
}) {
  const panel = ctl?.panels.find(p => p.cam.key === cam.key);
  const part = panel?.part ?? null, short = shortLabel(cam), rest = cam.label.slice(short.length).replace(/^,\s*/, "");
  const depth = cam.parts.some(p => p.depth), moved = part && (part.source === "manual" || manual[`${cam.key}/${part.key}`]);
  let text = `${camName(cam)}: not recording at this moment`, open: string | null = null, offNote: { text: string; go: number | null } | null = null;
  if (part && panel) {
    text = `${camName(cam)} · ${part.key} · frame ${num(panel.frame)} · ${fmtClock(ctl!.lib.clockOfFrame(cam, part, panel.frame), 2)}${moved ? " · moved by hand" : part.source === "unplaced" ? " · not on the clock yet, at a first guess" : ""}`;
    open = watchHref(job, cam.key, part.key, `t=${timeOfFrame(part, panel.frame).toFixed(3)}&c=${clock.toFixed(4)}`, panel.file === part.depth); // the shown frame's own moment
  } else {
    const next = cam.parts.find(p => p.clock_from > clock), prev = [...cam.parts].reverse().find(p => p.clock_to <= clock);
    offNote = next
      ? { text: `${prev ? "Picks up again" : "Starts"} with ${next.key} at ${fmtClock(next.clock_from, 2)}, in ${waitText(next.clock_from - clock)}.`, go: next.clock_from }
      : { text: `Stopped with ${prev?.key ?? "its last file"} at ${fmtClock(prev?.clock_to ?? clock, 2)}.`, go: null };
  }
  const big = focus === cam.key;
  return (
    <figure className={`panel card${big ? " is-big" : ""}`} data-cam={cam.key} data-on={!!part}>
      <div className="panel-head">
        <h2>{short}{rest && <span className="sub">, {rest}</span>}</h2>
        {depth && (
          <div className="seg" role="group" aria-label="Depth camera picture">
            <button type="button" aria-pressed={stream === "colour"} onClick={() => setStream("colour")}>Colour</button>
            <button type="button" aria-pressed={stream === "depth"} onClick={() => setStream("depth")}>Depth</button>
          </div>
        )}
      </div>
      <div className="pv" role="button" tabIndex={0} aria-pressed={big} aria-label={big ? `Show all cameras alike` : `Make ${short} large`}
        title={big ? "Click to show all cameras alike" : "Click to make this camera large"}
        onClick={() => setFocus(f => (f === cam.key ? null : cam.key))}
        onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); setFocus(f => (f === cam.key ? null : cam.key)); } }}>
        <video ref={register} preload="metadata" playsInline muted data-cam={cam.key} />
        {part && source === cam.key && <span className="pv-sound"><Icon name="vol" />Sound</span>}
        {part && caption && <div className="cc" data-caption><Spoken text={caption} /></div>}
        {offNote && (
          <div className="pv-off" onClick={e => e.stopPropagation()}>
            <Icon name="camoff" />
            <strong>Not recording at this moment</strong>
            <span>{offNote.text}</span>
            {offNote.go != null && <button type="button" className="btn secondary sm" onClick={() => ctl?.showClock(offNote.go!)}>Go to {fmtClock(offNote.go, 2)}</button>}
          </div>
        )}
      </div>
      <figcaption>
        <span className="cap-text" data-panel-caption>{text}</span>
        {open && <NavLink className="cap-open" href={open} title={`Open ${short} ${part?.key} in single view at this moment`}>Open<Icon name="open" /></NavLink>}
      </figcaption>
    </figure>
  );
}

const Scrubber = memo(function Scrubber({ s, clock, source, density, onGo }: { s: Session; clock: number; source: string; density: number[]; onGo: (c: number) => void }) {
  const { min, max } = s.clock, events = flagMarks(s), span = max - min, X = (t: number) => ((t - min) / span) * 100;
  const area = useRef<HTMLDivElement>(null);
  const at = (e: React.PointerEvent) => { const r = area.current!.getBoundingClientRect(); return min + Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * span; };
  const ticks: number[] = [];
  for (let m = Math.ceil(min / 600) * 10; m * 60 <= max; m += 10) ticks.push(m);
  return (
    <div className="scrub">
      <div className="sc-names" aria-hidden="true">
        <span style={{ height: 25 }} />
        <span style={{ height: 28 }}>Speech</span>
        <span style={{ height: 5 }} />
        {s.cameras.map(c => <span key={c.key} style={{ height: 24 }}>{camName(c)}</span>)}
      </div>
      <div className="sc-area" ref={area} role="slider" tabIndex={-1} aria-label="Session clock" aria-valuemin={Math.round(min)} aria-valuemax={Math.round(max)} aria-valuenow={Math.round(clock)} aria-valuetext={fmtClock(clock, 2)}
        onPointerDown={e => { if ((e.target as HTMLElement).closest("button")) return; e.currentTarget.setPointerCapture(e.pointerId); onGo(at(e)); }}
        onPointerMove={e => { if (e.buttons & 1 && e.currentTarget.hasPointerCapture(e.pointerId)) onGo(at(e)); }}>
        <div className="sc-marks">
          {events.map(e => {
            const x = X(e.clock);
            return <button type="button" key={e.key} className={`sc-ev${x < 8 ? " l" : x > 92 ? " r" : ""}`} style={{ left: `${x}%` }} onClick={() => onGo(e.clock)} data-jump={e.key} title={`${e.label}, flagged by the clock step: go to its start, ${fmtClock(e.clock, 2)}`} aria-label={`Go to the start of ${e.label}, flagged by the clock step`}><Icon name="flag" /></button>;
          })}
        </div>
        <Density density={density} min={min} max={max} clock={clock} />
        <div className="sc-lanes">
          {s.cameras.map(c => {
            const cur = partAt(c, clock);
            return (
              <div key={c.key} className={`sc-lane${c.key === source ? " is-src" : ""}`} data-cam={c.key}>
                {c.parts.map(p => (
                  <span key={p.key} className={`sc-bar${p === cur ? " cur" : ""}`} style={{ left: `${X(p.clock_from)}%`, width: `calc(${X(p.clock_to) - X(p.clock_from)}% - 2px)` }} title={`${shortLabel(c)} ${p.key}: ${fmtClock(p.clock_from, 2)} to ${fmtClock(p.clock_to, 2)}`}>
                    {(p.seconds / span) * 1250 > 50 ? p.key : ""}
                  </span>
                ))}
              </div>
            );
          })}
        </div>
        <div className="sc-axis" aria-hidden="true">{ticks.map(m => <span key={m} style={{ left: `${X(m * 60)}%` }}>{m}:00</span>)}</div>
        {events.map(e => <i key={e.key} className="sc-evl" style={{ left: `${X(e.clock)}%` }} />)}
        <div className="sc-head" style={{ left: `${X(clock)}%` }} data-t={fmtClock(clock, 2)} />
      </div>
    </div>
  );
});

const Density = memo(function Density({ density, min, max, clock }: { density: number[]; min: number; max: number; clock: number }) {
  const first = Math.floor(min / 60), span = max - min, peak = Math.max(1, ...density), cur = Math.floor(clock / 60) - first;
  return (
    <div className="sc-dens" aria-hidden="true">
      <svg viewBox="0 0 1000 34" preserveAspectRatio="none">
        {density.map((n, i) => {
          if (!n) return null;
          const a = Math.max(min, (first + i) * 60), b = Math.min(max, (first + i + 1) * 60), h = Math.max(2, (n / peak) * 32);
          return <rect key={i} className={i === cur ? "cur" : undefined} x={((a - min) / span) * 1000} width={Math.max(0.6, ((b - a) / span) * 1000 - 1.4)} y={34 - h} height={h} />;
        })}
      </svg>
    </div>
  );
}, (a, b) => a.density === b.density && Math.floor(a.clock / 60) === Math.floor(b.clock / 60));

// Each part's transcript is fetched when the clock first reaches it.
// file: "cam/part".
function useTranscript(job: string, file: string | null, has: boolean, cache: Map<string, Transcript>, gen: number): Transcript {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!file || !has || cache.has(file)) return;
    cache.set(file, { state: "loading", cues: [] });
    fetch(`/api/transcript?video=${encodeURIComponent(`${job}/${file}`)}`, { cache: "no-store" })
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(j => cache.set(file, j.exists ? { state: "ready", cues: j.cues } : { state: "none", cues: [] }))
      .catch(() => cache.set(file, { state: "error", cues: [] }))
      .finally(() => bump(n => n + 1));
  }, [job, file, has, cache, gen]);
  if (!file || !has) return { state: "none", cues: [] };
  return cache.get(file) ?? { state: "loading", cues: [] };
}

function Strip({ job, cam, part, sound, source, setSource, clock, transcript, nowCue, fileMs, captions, setCaptions, onGo }: {
  job: string; cam: Camera | null; part: Part | null; sound: Camera[]; source: string; setSource: (k: string) => void; clock: number;
  transcript: Transcript; nowCue: number; fileMs: number; captions: boolean; setCaptions: (v: boolean) => void; onGo: (c: number) => void;
}) {
  const row = useRef<HTMLDivElement>(null);
  const userAt = useRef(-Infinity);
  const cues = transcript.cues;
  const focusIndex = nowCue >= 0 ? nowCue : cues.findIndex(c => c.startMs > fileMs);
  useEffect(() => {
    const el = row.current, card = el?.querySelector<HTMLElement>(`[data-i="${focusIndex}"]`);
    if (!el || !card || performance.now() - userAt.current < 3000) return;
    const left = card.offsetLeft - (el.clientWidth - card.offsetWidth) / 2;
    el.scrollTo({ left, behavior: Math.abs(left - el.scrollLeft) > el.clientWidth ? "auto" : "smooth" });
  }, [focusIndex, part?.file, transcript.state]);
  const cueClock = (c: Cue) => (part ? part.clock_from + c.startMs / 1000 : 0);
  return (
    <section className="card strip" aria-label="Transcript following the clock" data-strip>
      <header className="strip-head">
        <h2>Transcript <span>· {cam ? shortLabel(cam) : "no camera"}</span></h2>
        <div className="seg" role="group" aria-label="Sound and transcript from">
          {sound.map(c => <button type="button" key={c.key} aria-pressed={c.key === source} onClick={() => setSource(c.key)}>{c.key === source && <Icon name="vol" />}{shortLabel(c)}</button>)}
        </div>
        <span className={`live${part ? "" : " off"}`}><i />{part ? "Following the clock" : "Not recording"}</span>
        <label className="switch" title="Show the cue on the picture of the source camera">
          <input type="checkbox" checked={captions} onChange={e => setCaptions(e.target.checked)} />
          <span className="sw" aria-hidden="true" /><span>Captions</span>
        </label>
        <span className="grow" />
        {part && transcript.state === "ready" && <span className="lbl num" data-strip-where>{part.key} · {nowCue >= 0 ? `cue ${num(nowCue + 1)} of ${num(cues.length)}` : `${num(cues.length)} cues`}</span>}
      </header>
      <div className="strip-row" ref={row} onWheel={() => (userAt.current = performance.now())} onPointerDown={() => (userAt.current = performance.now())}>
        {!cam || !part ? <p className="strip-note">{cam ? `${shortLabel(cam)} is not recording at this moment.` : "No camera with sound in this session."}</p>
          : transcript.state === "loading" ? <p className="strip-note">Loading the transcript of {part.key}…</p>
          : transcript.state === "error" ? <p className="strip-note">The transcript of {part.key} could not be read.</p>
          : transcript.state === "none" || !cues.length ? (
            <p className="strip-note">No transcript for {part.key} yet. <NavLink href={watchHref(job, cam.key, part.key, `t=${Math.max(0, fileMs / 1000).toFixed(3)}&c=${clock.toFixed(4)}`)} className="btn link sm">Open it in single view to start one</NavLink></p>
          ) : cues.map((c, i) => (
            <StripCard key={`${part.file}-${i}`} i={i} c={c} part={part} now={i === nowCue} progress={i === nowCue ? (fileMs - c.startMs) / Math.max(1, c.endMs - c.startMs) : 0}
              href={watchHref(job, cam.key, part.key, `t=${(c.startMs / 1000).toFixed(3)}&edit=1&c=${cueClock(c).toFixed(4)}`)} onGo={() => onGo(cueClock(c))} />
          ))}
      </div>
    </section>
  );
}

const StripCard = memo(function StripCard({ i, c, part, now, progress, href, onGo }: { i: number; c: Cue; part: Part; now: boolean; progress: number; href: string; onGo: () => void }) {
  return (
    <article className={`scard${now ? " is-now" : ""}`} data-i={i} data-now={now || undefined} onClick={e => { if (!(e.target as HTMLElement).closest("a, button")) onGo(); }}>
      <div className="scard-t">
        <button type="button" className="scard-go" onClick={onGo} aria-label={`Go to ${fmtClock(part.clock_from + c.startMs / 1000, 2)}`}>{fmtClock(part.clock_from + c.startMs / 1000, 2)}</button>
        <span className="ft">· {part.key} {fmtClock(c.startMs / 1000, 2)}</span>
      </div>
      <p><Spoken text={c.text} /></p>
      {now && <div className="prog"><i style={{ width: `${Math.min(100, Math.max(0, progress * 100))}%` }} /></div>}
      <NavLink className="scard-edit" href={href}><Icon name="edit" />Edit in single view</NavLink>
    </article>
  );
});
