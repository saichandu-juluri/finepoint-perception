// The one-clock engine: showClock puts every camera on its file and nearest
// frame, timeupdate re-aligns cameras that drift more than 0.25 s, ended moves on to the next part.
// React only reads it for display.
import { clockOf, clockOfFrame, frameAt, partAt, period, timeOfClock, timeOfFrame, type Camera, type Part, type Session } from "@/lib/session";

// pending: the seek that waits for a new file's metadata.
export type Panel = { cam: Camera; video: HTMLVideoElement; part: Part | null; file: string | null; frame: number; pending: (() => void) | null };

// A new file starts at currentTime 0, so a video's time is its position only once it has metadata and its seek has run.
const hasPosition = (p: Panel) => !p.pending && p.video.readyState >= 1;

export class OneClock {
  readonly lib = { partAt, period, frameAt, timeOfFrame, clockOfFrame, clockOf, timeOfClock };
  panels: Panel[];
  playing = false;
  clock: number;
  stream: "colour" | "depth" = "colour";
  version = 0;
  readonly s: Session;
  private url: (file: string) => string;
  private listeners = new Set<() => void>();
  private off: (() => void)[] = [];
  private raf = 0;

  constructor(s: Session, videos: Map<string, HTMLVideoElement>, url: (file: string) => string, clock: number) {
    this.s = s;
    this.url = url;
    this.clock = clock;
    this.panels = s.cameras.map(cam => ({ cam, video: videos.get(cam.key)!, part: null, file: null, frame: 0, pending: null }));
  }

  subscribe = (f: () => void) => { this.listeners.add(f); return () => { this.listeners.delete(f); }; };
  private changed() { this.version++; this.listeners.forEach(f => f()); }

  showClock(c: number) {
    // Just short of max: a part counts up to, not at, its clock_to.
    this.clock = Math.max(this.s.clock.min, Math.min(this.s.clock.max - 1e-3, c));
    for (const panel of this.panels) {
      // A seek still waiting for metadata is replaced, or it would run after this one.
      if (panel.pending) { panel.video.removeEventListener("loadedmetadata", panel.pending); panel.pending = null; }
      const p = partAt(panel.cam, this.clock);
      if (!p) {
        if (panel.part) { panel.video.removeAttribute("src"); panel.video.load(); }
        panel.part = panel.file = null;
        continue;
      }
      const file = this.stream === "depth" && p.depth ? p.depth : p.file;
      if (panel.file !== file) { panel.part = p; panel.file = file; panel.video.src = this.url(file); }
      const f = frameAt(panel.cam, p, this.clock);
      panel.frame = f;
      const seek = () => { panel.pending = null; panel.video.currentTime = timeOfFrame(p, f); this.caption(panel); this.changed(); if (this.playing) panel.video.play().catch(() => {}); };
      if (panel.video.readyState >= 1) seek(); else panel.video.addEventListener("loadedmetadata", (panel.pending = seek), { once: true });
    }
    this.changed();
  }

  togglePlay() {
    const c = this.shownClock(); // while playing, this.clock is up to one timeupdate (250 ms) behind the cameras
    this.playing = !this.playing;
    for (const p of this.panels) if (p.part) { if (this.playing) p.video.play().catch(() => {}); else p.video.pause(); }
    cancelAnimationFrame(this.raf);
    if (this.playing) this.raf = requestAnimationFrame(this.tick);
    else this.showClock(c);
    this.changed();
  }

  setStream(stream: "colour" | "depth") { this.stream = stream; this.showClock(this.shownClock()); }

  // Only the source camera is heard.
  setSource(key: string) { for (const p of this.panels) p.video.muted = p.cam.key !== key; }

  // The clock to display: between timeupdates it follows the first playing camera every frame.
  shownClock(): number {
    const lead = this.playing ? this.panels.find(p => p.part) : null;
    return lead && lead.part && hasPosition(lead) ? clockOf(lead.cam, lead.part, lead.video.currentTime) : this.clock;
  }

  private tick = () => { this.changed(); if (this.playing) this.raf = requestAnimationFrame(this.tick); };

  private caption(panel: Panel) {
    const p = panel.part;
    if (p && hasPosition(panel)) panel.frame = Math.min(p.frames - 1, Math.floor(panel.video.currentTime * p.fps));
  }

  attach() {
    for (const panel of this.panels) {
      const timeupdate = () => {
        if (!this.playing || !panel.part || !hasPosition(panel) || panel !== this.panels.find(p => p.part)) return;
        this.clock = clockOf(panel.cam, panel.part, panel.video.currentTime);
        for (const other of this.panels) {
          if (!other.part) continue;
          this.caption(other);
          if (other === panel) continue;
          const p = partAt(other.cam, this.clock);
          if (p !== other.part) { this.showClock(this.clock); return; }
          const t = timeOfFrame(p, frameAt(other.cam, p, this.clock));
          if (other.video.readyState >= 2 && !other.video.seeking && Math.abs(other.video.currentTime - t) > 0.25) other.video.currentTime = t;
        }
        for (const other of this.panels) if (!other.part && partAt(other.cam, this.clock)) { this.showClock(this.clock); return; }
        this.changed();
      };
      // On to the moment after this part; when no camera records there, the session is over.
      const ended = () => {
        if (!this.playing || !panel.part) return;
        const c = clockOf(panel.cam, panel.part, panel.video.duration) + 0.05;
        if (this.panels.some(p => partAt(p.cam, c))) this.showClock(c); else this.togglePlay();
      };
      panel.video.addEventListener("timeupdate", timeupdate);
      panel.video.addEventListener("ended", ended);
      this.off.push(() => { panel.video.removeEventListener("timeupdate", timeupdate); panel.video.removeEventListener("ended", ended); });
    }
  }

  detach() {
    if (this.playing) this.togglePlay();
    cancelAnimationFrame(this.raf);
    this.off.forEach(f => f());
    this.off = [];
    for (const p of this.panels) p.video.pause();
  }
}
