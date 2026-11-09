"use client";
// Lanes under the video: a zoomed "Cues" lane of numbered cue blocks around the playhead, and the whole file with the cue
// marks, the zoom window and the playhead. Click or drag to seek; the whole-file lane shows the time on hover.
import { memo, useEffect, useRef, useState } from "react";
import { cueIndexAt, fmtClock } from "@/lib/srt";
import type { EdCue } from "./transcript";
import { usePlayhead, type PlayheadStore, type VideoControls } from "./playhead";

const ZOOM_S = 30;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function Timeline({ cues, editing, changed, bad, store, ctl, onOpen }: {
  cues: EdCue[]; editing: number | null; changed: (c: EdCue) => boolean; bad: (c: EdCue) => boolean;
  store: PlayheadStore; ctl: VideoControls; onOpen: (id: number) => void;
}) {
  const t = usePlayhead(store, p => Math.round(p.t * 100) / 100);
  const dur = usePlayhead(store, p => p.duration);
  const now = cueIndexAt(cues, Math.round(t * 1000));
  const [zoomRef, zw] = useWidth<HTMLDivElement>();
  const a = t - ZOOM_S / 2, P = (s: number) => ((s - a) / ZOOM_S) * 100, pxs = zw / ZOOM_S;

  const ticks = [];
  for (let s = Math.ceil(Math.max(0, a)); s <= a + ZOOM_S; s++) {
    ticks.push(<i key={`t${s}`} className={`zt${s % 5 ? "" : " big"}`} style={{ left: `${P(s)}%` }} />);
    if (s % 5 === 0) ticks.push(<span key={`l${s}`} className="zl" style={{ left: `${P(s)}%` }}>{fmtClock(s, 0)}</span>);
  }
  const zoomClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const blk = (e.target as HTMLElement).closest<HTMLElement>("[data-id]");
    if (blk) return onOpen(+blk.dataset.id!);
    const r = e.currentTarget.getBoundingClientRect();
    ctl.seek(a + ((e.clientX - r.left) / r.width) * ZOOM_S);
  };

  return (
    <div className="tl2">
      <span className="lab lz">Cues</span>
      <div className="zoom" ref={zoomRef} onClick={zoomClick} role="group" aria-label="Cues around the playhead; click a cue to edit it">
        <div className="zr" aria-hidden="true">{ticks}</div>
        <div className="zc">
          {cues.map((c, i) => {
            const s0 = c.startMs / 1000, s1 = c.endMs / 1000;
            if (s1 < a || s0 > a + ZOOM_S) return null;
            const w = (s1 - s0) * pxs, cls = `blk${i === now ? " now" : ""}${c.id === editing ? " edit" : ""}${changed(c) ? " chg" : ""}${bad(c) ? " bad" : ""}`;
            return (
              <div key={c.id} className={cls} data-id={c.id} style={{ left: `${P(s0)}%`, width: `${Math.max(0.4, ((s1 - s0) / ZOOM_S) * 100)}%` }} title={`${i + 1}: ${c.text || "(no text yet)"}`}>
                {w >= 30 && <b>{i + 1}</b>}
                {w >= 70 && <span>{c.text}</span>}
              </div>
            );
          })}
        </div>
        {cues.length === 0 && <div className="zempty">No cues yet. Press <kbd>N</kbd> to add one at the playhead.</div>}
        <div className="ph" style={{ left: "50%" }} />
      </div>
      <span className="lab lo">Whole file</span>
      <Overview cues={cues} now={now} t={t} dur={dur} ctl={ctl} />
    </div>
  );
}

function Overview({ cues, now, t, dur, ctl }: { cues: EdCue[]; now: number; t: number; dur: number; ctl: VideoControls }) {
  const [hover, setHover] = useState<number | null>(null);
  const timeAt = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * dur;
  };
  const pct = (s: number) => (dur ? (s / dur) * 100 : 0);
  const ticks: number[] = [];
  const step = [10, 15, 30, 60, 120, 300, 600, 900, 1800].find(s => dur / s <= 8) ?? 3600;
  for (let s = 0; s <= dur - step * 0.75; s += step) ticks.push(s);
  return (
    <div>
      <div
        className="ov" role="slider" tabIndex={0} aria-label="Position in the file" aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(t)} aria-valuetext={fmtClock(t, 2)}
        onPointerDown={e => {
          if (!dur) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          ctl.seek(timeAt(e));
        }}
        onPointerMove={e => {
          if (!dur) return;
          setHover(timeAt(e));
          if (e.buttons & 1) ctl.seek(timeAt(e));
        }}
        onPointerLeave={() => setHover(null)}
      >
        <Marks cues={cues} dur={dur} now={now} />
        <div className="win" style={{ left: `${pct(Math.max(0, t - ZOOM_S / 2))}%`, width: `${pct(Math.min(dur, t + ZOOM_S / 2) - Math.max(0, t - ZOOM_S / 2))}%` }} />
        <div className="ph" style={{ left: `${pct(t)}%` }} />
        {hover != null && <span className="ov-tip" style={{ left: `${pct(hover)}%` }}>{fmtClock(hover, 1)}</span>}
      </div>
      <div className="oruler" aria-hidden="true">
        {ticks.map(s => <span key={s} style={{ left: `${pct(s)}%` }}>{fmtClock(s, 0)}</span>)}
        {dur > 0 && <span style={{ left: "100%" }}>{fmtClock(dur, 0)}</span>}
      </div>
    </div>
  );
}

const Marks = memo(function Marks({ cues, dur, now }: { cues: EdCue[]; dur: number; now: number }) {
  if (!dur) return null;
  return (
    <svg viewBox="0 0 1000 26" preserveAspectRatio="none" aria-hidden="true">
      {cues.map((c, i) => (
        <rect key={c.id} className={i === now ? "now" : undefined} x={(c.startMs / 1000 / dur) * 1000} y={6} height={14} width={Math.max(1, ((c.endMs - c.startMs) / 1000 / dur) * 1000)} />
      ))}
    </svg>
  );
});
