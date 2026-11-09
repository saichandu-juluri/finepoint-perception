// The video's position, outside React state: rAF writes it up to 60 times a second, and each component subscribes to
// the part it shows (a cue index, the time in centiseconds), so most renders are skipped.
import { useSyncExternalStore } from "react";

export type Playhead = { t: number; playing: boolean; duration: number };

export function createPlayhead(t = 0) {
  let state: Playhead = { t, playing: false, duration: 0 };
  const subs = new Set<() => void>();
  return {
    get: () => state,
    set(patch: Partial<Playhead>) {
      state = { ...state, ...patch };
      subs.forEach(f => f());
    },
    subscribe(f: () => void) {
      subs.add(f);
      return () => { subs.delete(f); };
    },
  };
}
export type PlayheadStore = ReturnType<typeof createPlayhead>;

// `select` must return a primitive (or the same object) for an unchanged position, or React re-renders every frame.
export function usePlayhead<T>(store: PlayheadStore, select: (p: Playhead) => T): T {
  const get = () => select(store.get());
  return useSyncExternalStore(store.subscribe, get, get);
}

export type VideoControls = {
  toggle: () => void;
  seek: (t: number) => void;
  nudge: (dt: number) => void;
  step: (frames: number) => void;
};
