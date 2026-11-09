// A session on its clock (work/clock/clock.json, joined to work/cameras/cameras.json), and the one-clock maths. Pure.
// Every part runs on one straight line: clock = clock_from + frame * s_per_frame. For an action part that is clock.json's
// start_s and rate / fps exactly; for a depth part it is start_s (its first frame) and the frame period of the line
// fitted to its frames_csv.

export type Part = {
  key: string;
  file: string; // absolute path of the video
  depth?: string; // depth camera only: its depth video
  frames: number;
  fps: number;
  s_per_frame: number;
  seconds: number;
  clock_from: number;
  clock_to: number;
  start_s: number; // clock.json's own start_s, which a hand move replaces
  rate: number;
  source: string; // sound, chained, motion, reference, manual, an imported clock's word, or unplaced: left out of clock.json, at a first guess
  flag: string | null;
  sound: boolean;
};

export type Camera = { key: string; label: string; kind: string; sound: boolean; fps: number; parts: Part[] };

// A clock check item that names a part: the place a person should look.
export type Flag = { key: string; camera: string; part: string; status: string; name: string; message: string; fix: string };

export type Session = {
  id: string;
  name: string;
  clock: {
    min: number; max: number; zero: string; reference: string;
    existing: string | null; // [clock] existing: the imported clock file, which reads no hand moves
    evidence: Record<string, string>;
  };
  cameras: Camera[];
  off: string[]; // cameras of the project that are not on the clock
  flags: Flag[];
  manual: Record<string, { start_s: number; note: string }>; // decisions.json clock.user.manual
  needs: string[]; // "cam/part" of each part only the person can place: unplaced, or placed with no sound as a first guess
};

// The part recording at clock c; half a frame either side of a part still counts, unless another part holds c itself.
export const partAt = (cam: Camera, c: number) => cam.parts.find(p => c >= p.clock_from && c < p.clock_to)
  ?? cam.parts.find(p => c >= p.clock_from - p.s_per_frame / 2 && c < p.clock_to + p.s_per_frame / 2);
export const period = (_cam: Camera, p: Part) => p.s_per_frame;
export const frameAt = (cam: Camera, p: Part, c: number) => Math.max(0, Math.min(p.frames - 1, Math.round((c - p.clock_from) / period(cam, p))));
export const timeOfFrame = (p: Part, f: number) => (f + 0.5) / p.fps;
export const clockOfFrame = (cam: Camera, p: Part, f: number) => p.clock_from + f * period(cam, p);
export const clockOf = (cam: Camera, p: Part, t: number) => p.clock_from + t * p.fps * period(cam, p);
export const timeOfClock = (cam: Camera, p: Part, c: number) => (c - p.clock_from) / (p.fps * period(cam, p));
export const shortLabel = (cam: Camera) => cam.label.split(",")[0];

// Moves a part's start on the clock, keeping its rate: the hand move of Multi-view, before and after it is saved.
export function moveTo(p: Part, start_s: number) {
  const d = start_s - p.start_s;
  p.start_s = start_s;
  p.clock_from += d;
  p.clock_to += d;
}

// Whether a part can be moved by hand: any part but those the clock step placed as the reference camera, whose clock
// is the session's (a part of it without sound is only chained, so it can be).
export const movable = (s: Session, cam: Camera, p: Part) => cam.key !== s.clock.reference || ["chained", "manual", "unplaced"].includes(p.source);

// The markers of the clock lanes: each flagged part at its start, where it meets the part before.
export const flagMarks = (s: Session) => s.flags.flatMap(f => {
  const p = s.cameras.find(c => c.key === f.camera)?.parts.find(q => q.key === f.part);
  return p ? [{ key: f.key, label: `${f.camera} ${f.part}`, clock: p.clock_from }] : [];
});
