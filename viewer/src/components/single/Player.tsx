"use client";
// The video with its own controls, the captions from the editor's cues, and the two-level timeline.
import { useState, type ReactNode, type RefObject } from "react";
import { cueIndexAt, fmtClock } from "@/lib/srt";
import { num } from "@/lib/format";
import { Icon } from "../Icon";
import { useKey } from "../keys";
import { Spoken } from "../Spoken";
import type { EdCue } from "./transcript";
import { usePlayhead, type PlayheadStore, type VideoControls } from "./playhead";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

export function Player({ src, videoRef, boxRef, store, ctl, cues, captions, setCaptions, fps, clockAt, timeline }: {
  src: string; videoRef: RefObject<HTMLVideoElement | null>; boxRef: RefObject<HTMLDivElement | null>; store: PlayheadStore; ctl: VideoControls;
  cues: EdCue[]; captions: boolean; setCaptions: (v: boolean) => void; fps: number; clockAt: ((t: number) => number) | null; timeline: ReactNode;
}) {
  const playing = usePlayhead(store, p => p.playing);
  const [speed, setSpeed] = useState(1);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [failed, setFailed] = useState(false);
  const key = useKey();
  const setVol = (v: number, m: boolean) => {
    setVolume(v); setMuted(m);
    const el = videoRef.current;
    if (el) { el.volume = v; el.muted = m; }
  };
  return (
    <section className="player card" aria-label="Video">
      <div className="video-box" ref={boxRef}>
        <video ref={videoRef} src={src} preload="metadata" playsInline onClick={ctl.toggle} onError={() => setFailed(true)} data-main-video />
        {captions && <Caption store={store} cues={cues} />}
        {failed && <div className="vb-msg">This video could not be played in the browser.</div>}
      </div>
      <div className="transport" style={{ flexWrap: "wrap" }}>
        <button type="button" className="play" onClick={ctl.toggle} aria-label={playing ? "Pause (Space)" : "Play (Space)"} title={playing ? "Pause (Space or K)" : "Play (Space or K)"}>
          <Icon name={playing ? "pause" : "play"} />
        </button>
        <button type="button" className="icon-btn flat" onClick={() => ctl.nudge(-5)} aria-label="Back 5 seconds (J)" title="Back 5 s (J)"><Icon name="back5" /></button>
        <button type="button" className="icon-btn flat" onClick={() => ctl.step(-1)} aria-label="One frame back (←)" title="One frame back (←)"><Icon name="fback" /></button>
        <button type="button" className="icon-btn flat" onClick={() => ctl.step(1)} aria-label="One frame forward (→)" title="One frame forward (→)"><Icon name="ffwd" /></button>
        <button type="button" className="icon-btn flat" onClick={() => ctl.nudge(5)} aria-label="Forward 5 seconds (L)" title="Forward 5 s (L)"><Icon name="fwd5" /></button>
        <TimeBlock store={store} fps={fps} clockAt={clockAt} />
        <span className="grow" />
        <label className="switch" title="Show the cue at the playhead on the picture (C)">
          <input type="checkbox" checked={captions} onChange={e => setCaptions(e.target.checked)} />
          <span className="sw" aria-hidden="true" />
          <span>Captions</span>
        </label>
        <label>
          <span className="sr">Playback speed</span>
          <select className="select" value={speed} onChange={e => { const v = +e.target.value; setSpeed(v); if (videoRef.current) videoRef.current.playbackRate = v; }}>
            {SPEEDS.map(v => <option key={v} value={v}>{v}×</option>)}
          </select>
        </label>
        <div className="vol">
          <button type="button" className="icon-btn flat sm" onClick={() => setVol(volume, !muted)} aria-label={muted ? "Sound on" : "Mute"} title={muted ? "Sound on" : "Mute"}>
            <Icon name={muted || volume === 0 ? "mute" : "vol"} />
          </button>
          <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} aria-label="Volume" onChange={e => setVol(+e.target.value, false)} />
        </div>
        <button type="button" className="icon-btn flat" aria-label="Full screen" title="Full screen"
          onClick={() => (document.fullscreenElement ? document.exitFullscreen() : boxRef.current?.requestFullscreen())?.catch(() => {})}>
          <Icon name="full" />
        </button>
      </div>
      {timeline}
      <div className="hints" aria-label="Keyboard shortcuts">
        <span><kbd>Space</kbd>Play or pause</span>
        <span><kbd>J</kbd><kbd>L</kbd>Back or forward 5 s</span>
        <span><kbd>←</kbd><kbd>→</kbd>One frame</span>
        <span><kbd>N</kbd>New cue at the playhead</span>
        <span><kbd>{key("cmd+S")}</kbd>Save</span>
      </div>
    </section>
  );
}

function Caption({ store, cues }: { store: PlayheadStore; cues: EdCue[] }) {
  const i = usePlayhead(store, p => cueIndexAt(cues, Math.round(p.t * 1000)));
  const text = i >= 0 ? cues[i].text.trim() : "";
  return text ? <div className="cc" data-caption><Spoken text={text} /></div> : null;
}

function TimeBlock({ store, fps, clockAt }: { store: PlayheadStore; fps: number; clockAt: ((t: number) => number) | null }) {
  const t = usePlayhead(store, p => Math.round(p.t * 100) / 100);
  const f = usePlayhead(store, p => Math.floor(p.t * fps + 1e-6)); // the frame on screen, from the unrounded playhead
  const dur = usePlayhead(store, p => p.duration);
  return (
    <div className="time-block">
      <div className="time"><b data-time>{fmtClock(t, 2)}</b><span>/</span>{dur ? fmtClock(dur, 2) : "–"}</div>
      <div className="frame">frame {num(f)}{clockAt ? <> · clock <span className="mono">{fmtClock(clockAt(f / fps), 2)}</span></> : null}</div>
    </div>
  );
}
