// Everything the app reads about camrig sessions. A session is a folder holding project.toml, found under the folders
// named in CAMRIG_SESSIONS (default <camrig>/sessions); a folder named there may also be a session itself. The project
// file is read by camrig's own Python (scripts/camrig_info.py), so the app refuses a project exactly when a run would.
// Work files are read by their place in docs/FORMATS.md (work/<step>/...), never by the absolute paths inside them.
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";
import { notFound } from "next/navigation";
import type { Camera, Flag, Part, Session } from "@/lib/session";
import { HttpError, statOrNull } from "./data";

export const REPO = path.resolve(process.env.CAMRIG_REPO ?? path.join(process.cwd(), ".."));
export const PY = process.env.CAMRIG_PYTHON ?? path.join(REPO, ".pixi", "envs", "core", ...(process.platform === "win32" ? ["python.exe"] : ["bin", "python"]));
export const ROOTS = (process.env.CAMRIG_SESSIONS ?? path.join(REPO, "sessions")).split(path.delimiter).filter(Boolean).map(p => path.resolve(p));

export type ProjectCamera = { name: string; kind: string; folder: string; exports: Record<string, string>; videos: number };
export type Project =
  | { ok: true; name: string; clock_existing: string | null; cameras: ProjectCamera[];
      needs: Record<string, string[]> } // the steps each step builds on in this project, as camrig's runner sees them
  | { ok: false; error: string };
export type SessionRef = { id: string; dir: string; toml: string };

export type Item = { name: string; status: string; value?: unknown; limit?: string | null; unit?: string | null; message?: string; fix?: string; where?: Record<string, unknown> };
export type Check = { step: string; status: string; items: Item[]; numbers?: Record<string, unknown>; started?: string; seconds?: number; outputs?: Record<string, string> };

const missing = (e: unknown) => ["ENOENT", "ENOTDIR"].includes((e as NodeJS.ErrnoException)?.code ?? "");

export function checkPython(): void {
  try { fs.accessSync(PY, fs.constants.X_OK); } catch {
    throw new HttpError(503, `The app's Python was not found at ${PY}. Run pixi install -e core --locked in the app's folder, or start the app with CAMRIG_PYTHON=/path/to/python.`);
  }
}

// Python reads and writes text in UTF-8 only in its UTF-8 mode on Windows (else in the ANSI code page, such as cp1252),
// and -I ignores PYTHONUTF8, so a -I run asks for it itself.
export const PY_UTF8 = process.platform === "win32" ? ["-X", "utf8"] : [];

function helper(tomls: string[]): Promise<{ steps: string[]; projects: Record<string, Project> }> {
  checkPython();
  return new Promise((ok, no) => execFile(PY, [...PY_UTF8, "-I", path.join(REPO, "viewer", "scripts", "camrig_info.py"), ...tomls], { timeout: 60_000, maxBuffer: 1 << 24, windowsHide: true },
    (e, out, err) => (e ? no(new HttpError(500, `The project files could not be read: ${String(err || e.message).trim().slice(-400)}`)) : ok(JSON.parse(out)))));
}

const memo = globalThis as typeof globalThis & { camrigProjects?: Map<string, { mtime: number; p: Project }>; camrigSteps?: string[] };
const cache = (memo.camrigProjects ??= new Map());

// Each project file as camrig reads it, read again when the file changed.
export async function projects(tomls: string[]): Promise<Record<string, Project>> {
  const stats = await Promise.all(tomls.map(t => statOrNull(t)));
  const stale = tomls.filter((t, i) => cache.get(t)?.mtime !== stats[i]?.mtimeMs);
  if (stale.length || !memo.camrigSteps) {
    const r = await helper(stale);
    memo.camrigSteps = r.steps;
    stale.forEach(t => cache.set(t, { mtime: stats[tomls.indexOf(t)]?.mtimeMs ?? 0, p: r.projects[t] }));
  }
  return Object.fromEntries(tomls.map(t => [t, cache.get(t)!.p]));
}
export const project = async (ref: SessionRef) => (await projects([ref.toml]))[ref.toml];

// The step order of camrig/run.py.
export async function steps(): Promise<string[]> {
  if (!memo.camrigSteps) await projects([]);
  return memo.camrigSteps!;
}


const hasToml = (dir: string) => statOrNull(path.join(dir, "project.toml")).then(s => !!s?.isFile());

// ponytail: a session's id is its folder name; a second folder of the same name elsewhere is left out.
export async function listSessions(): Promise<SessionRef[]> {
  const out: SessionRef[] = [];
  const add = (dir: string) => { if (!out.some(s => s.id === path.basename(dir))) out.push({ id: path.basename(dir), dir, toml: path.join(dir, "project.toml") }); };
  for (const root of ROOTS) {
    if (await hasToml(root)) { add(root); continue; }
    const entries = await fsp.readdir(root, { withFileTypes: true }).catch(() => []);
    const dirs = entries.filter(e => e.isDirectory() && !e.name.startsWith(".")).map(e => path.join(root, e.name)).sort();
    const ok = await Promise.all(dirs.map(hasToml));
    dirs.filter((_, i) => ok[i]).forEach(add);
  }
  return out;
}

export async function sessionRef(id: string): Promise<SessionRef | null> {
  return (await listSessions()).find(s => s.id === id) ?? null;
}

// For pages: an unknown session is a 404.
export async function requireSession(id: string): Promise<SessionRef> {
  return (await sessionRef(id)) ?? notFound();
}

export async function apiSession(id: unknown): Promise<SessionRef> {
  const ref = typeof id === "string" ? await sessionRef(id) : null;
  if (!ref) throw new HttpError(404, "No such session");
  return ref;
}

export async function readJson<T = any>(file: string): Promise<T | null> {
  const text = await fsp.readFile(file, "utf8").catch(e => (missing(e) ? null : Promise.reject(e)));
  if (text == null) return null;
  try { return JSON.parse(text) as T; } catch { return null; }
}

export const work = (ref: SessionRef, step: string, file = "") => path.join(ref.dir, "work", step, file);
export const readCheck = (ref: SessionRef, step: string) => readJson<Check>(work(ref, step, "check.json"));

export async function readChecks(ref: SessionRef): Promise<Record<string, Check | null>> {
  const names = await steps();
  const all = await Promise.all(names.map(n => readCheck(ref, n)));
  return Object.fromEntries(names.map((n, i) => [n, all[i]]));
}

// The folders a session may serve files from: its own folder, and the video and export folders its project.toml names.
export async function allowedRoots(ref: SessionRef): Promise<string[]> {
  const p = await project(ref).catch(() => null), roots = [ref.dir];
  if (p?.ok) for (const c of p.cameras) roots.push(c.folder, ...Object.values(c.exports));
  const real = await Promise.all(roots.map(async r => {
    const st = await statOrNull(r);
    return st ? fsp.realpath(st.isDirectory() ? r : path.dirname(r)).catch(() => null) : null;
  }));
  return [...new Set(real.filter((r): r is string => !!r))];
}

// The absolute path a media URL names by its names (lib/format.ts mediaUrl): /a/b.mp4; on Windows C:\a\b.mp4 from
// C:/a/b.mp4, or \\server\share\b.mp4 from server/share/b.mp4.
export function mediaPath(parts: string[]): string {
  if (process.platform !== "win32") return "/" + parts.join("/");
  return /^[A-Za-z]:$/.test(parts[0] ?? "") ? `${parts[0]}\\${parts.slice(1).join("\\")}` : `\\\\${parts.join("\\")}`;
}

// A file the session may serve, by its absolute path: hidden names are refused, and its real place must be inside one of
// the allowed folders (links that lead elsewhere are refused too).
export async function resolveMedia(ref: SessionRef, abs: string): Promise<string> {
  if (process.platform === "win32") return resolveMediaWindows(ref, abs);
  const parts = abs.split("/").filter(Boolean);
  if (!path.isAbsolute(abs) || parts.some(p => p.startsWith(".") || p.includes("\0") || p.includes("\\"))) throw new HttpError(400, "Bad path");
  const real = await fsp.realpath(abs).catch(() => null);
  if (!real) throw new HttpError(404, "Not found");
  if (!(await allowedRoots(ref)).some(r => real === r || real.startsWith(r + path.sep))) throw new HttpError(403, "This file is outside the session and its video folders.");
  return real;
}

// Windows: C:\… or \\server\share\…. No name may be hidden, or hold ":" (but the drive) or "/". The path must lie in an
// allowed folder by its names before the disk is asked at all: opening \\host\share\… would send the person's Windows
// sign-in to that host. Paths are compared without case, as Windows compares them.
async function resolveMediaWindows(ref: SessionRef, abs: string): Promise<string> {
  const parts = abs.split("\\").filter(Boolean), names = /^[A-Za-z]:$/.test(parts[0] ?? "") ? parts.slice(1) : parts;
  if (!path.win32.isAbsolute(abs) || names.some(p => p.startsWith(".") || /[\0:/]/.test(p))) throw new HttpError(400, "Bad path");
  const inside = (file: string, roots: string[]) => roots.some(r => {
    const f = file.toLowerCase(), d = r.toLowerCase();
    return f === d || f.startsWith(d.endsWith("\\") ? d : d + "\\");
  });
  const p = await project(ref).catch(() => null), real = await allowedRoots(ref);
  const named = [ref.dir, ...(p?.ok ? p.cameras.flatMap(c => [c.folder, ...Object.values(c.exports)]) : []), ...real];
  if (!inside(abs, named)) throw new HttpError(403, "This file is outside the session and its video folders.");
  const file = await fsp.realpath(abs).catch(() => null);
  if (!file) throw new HttpError(404, "Not found");
  if (!inside(file, real)) throw new HttpError(403, "This file is outside the session and its video folders.");
  return file;
}

// cameras.json, as the cameras step wrote it.
// has_sound is left out: it is true only when every part has sound; each action part's own `sound` says it (a depth part has none).
export type CamerasJson = { cameras: { name: string; kind: string; station: string | null; lens: string | null;
  parts: { key: string; file: string; frames: number; fps: number; seconds: number; width?: number; height?: number; sound?: boolean; depth_video?: string; bag?: string; frames_csv?: string; missing?: number; created?: string }[] }[] };
export const readCameras = (ref: SessionRef) => readJson<CamerasJson>(work(ref, "cameras", "cameras.json"));

// The straight line through a depth part's colour frames: device time = a + b * index, fitted per part; the app uses b.
const fits = new Map<string, { mtime: number; n: number; a: number; b: number }>();
async function frameLine(csv: string): Promise<{ n: number; a: number; b: number } | null> {
  const st = await statOrNull(csv);
  if (!st) return null;
  const hit = fits.get(csv);
  if (hit?.mtime === st.mtimeMs) return hit;
  const lines = readline.createInterface({ input: fs.createReadStream(csv, { encoding: "utf8" }), crlfDelay: Infinity });
  let head: string[] | null = null, n = 0, d0 = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
  for await (const line of lines) {
    const cols = line.split(",");
    if (!head) { head = cols; continue; }
    const stream = head.indexOf("stream"), dev = head.indexOf("device_us");
    if (stream >= 0 && cols[stream] !== "colour") continue;
    const us = Number(cols[dev]);
    if (n === 0) d0 = us;
    const y = (us - d0) / 1e6;
    sx += n; sy += y; sxx += n * n; sxy += n * y; n++;
  }
  if (n < 2) return null;
  const b = (n * sxy - sx * sy) / (n * sxx - sx * sx), a = (sy - b * sx) / n, r = { mtime: st.mtimeMs, n, a, b };
  fits.set(csv, r);
  return r;
}

type ClockJson = { reference: string; zero: string; cameras: Record<string, { kind: string; parts: { key: string; file: string; frames: number; fps: number; start_s: number; rate?: number; frames_csv?: string; source?: string; flag?: string | null }[] }> };

// The session on its clock, or null when the clock step has made no clock.json. With `unplaced` (Multi-view), the parts
// of cameras.json that clock.json leaves out are added at a first guess, so they can be placed by hand: where a hand
// setting was saved for them, else one after another from the clock's start.
export async function loadSession(ref: SessionRef, unplaced = false): Promise<Session | null> {
  const [clock, cams, check, decisions, p] = await Promise.all([readJson<ClockJson>(work(ref, "clock", "clock.json")), readCameras(ref), readCheck(ref, "clock"),
    readJson<Record<string, any>>(path.join(ref.dir, "decisions.json")), project(ref).catch(() => null)]);
  if (!clock?.cameras) return null;
  const byFile = new Map((cams?.cameras ?? []).flatMap(c => c.parts.map(q => [q.file, { cam: c, part: q }] as const)));
  const cameras: Camera[] = [];
  for (const [key, c] of Object.entries(clock.cameras)) {
    const parts: Part[] = [];
    for (const q of c.parts) {
      const rate = q.rate ?? 1, known = byFile.get(q.file), line = q.frames_csv ? await frameLine(q.frames_csv) : null;
      // from is the first frame's clock, as clock.json defines start_s; a depth part's line gives only its frame period
      const frames = line?.n ?? q.frames, b = line ? rate * line.b : rate / q.fps, from = q.start_s;
      parts.push({
        key: known?.part.key ?? q.key, file: q.file, depth: known?.part.depth_video, frames, fps: q.fps, s_per_frame: b, seconds: frames / q.fps,
        clock_from: from, clock_to: line ? from + b * (frames - 1) : from + b * frames, start_s: q.start_s, rate,
        source: q.source ?? "", flag: q.flag ?? null, sound: known ? !!known.part.sound : c.kind !== "depth",
      });
    }
    parts.sort((x, y) => x.clock_from - y.clock_from);
    cameras.push(camera(key, c.kind, parts));
  }
  const manual = Object.fromEntries(Object.entries(decisions?.clock?.user?.manual ?? {}).map(([k, v]: [string, any]) =>
    [k, typeof v === "number" ? { start_s: v, note: "" } : { start_s: Number(v?.start_s), note: String(v?.note ?? "") }]));
  const min = Math.min(...cameras.flatMap(c => c.parts.map(x => x.clock_from)));
  if (unplaced && !(p?.ok && p.clock_existing)) {
    const placed = new Set(cameras.flatMap(c => c.parts.map(x => x.file)));
    for (const c of cams?.cameras ?? []) {
      let at = min;
      const left = c.parts.filter(q => !placed.has(q.file)).map(q => {
        const guess = manual[`${c.name}/${q.key}`]?.start_s ?? at, b = 1 / q.fps;
        at = guess + q.seconds;
        return { key: q.key, file: q.file, depth: q.depth_video, frames: q.frames, fps: q.fps, s_per_frame: b, seconds: q.seconds, clock_from: guess,
          clock_to: guess + b * q.frames, start_s: guess, rate: 1, source: "unplaced", flag: null, sound: !!q.sound } satisfies Part;
      });
      if (!left.length) continue;
      const cam = cameras.find(x => x.key === c.name);
      if (cam) { cam.parts.push(...left); cam.parts.sort((x, y) => x.clock_from - y.clock_from); } else cameras.push(camera(c.name, c.kind, left));
    }
  }
  const order = (p?.ok ? p.cameras.map(c => c.name) : cams?.cameras.map(c => c.name)) ?? [];
  cameras.sort((x, y) => (order.indexOf(x.key) + 1 || 99) - (order.indexOf(y.key) + 1 || 99));
  const all = cameras.flatMap(c => c.parts);
  const flags: Flag[] = (check?.items ?? []).filter(i => i.where?.part && i.where?.camera && (i.status === "flag" || i.status === "fail")).map(i => ({
    key: `${i.where!.camera}/${i.where!.part}`, camera: String(i.where!.camera), part: String(i.where!.part), status: i.status, name: i.name, message: i.message ?? "", fix: i.fix ?? "",
  }));
  // what only the person can place: a part left out of clock.json, or one placed without sound as a first guess
  const needs = cameras.flatMap(c => c.parts.filter(x => x.source === "unplaced" || x.flag?.startsWith("no sound")).map(x => `${c.key}/${x.key}`));
  return {
    id: ref.id, name: p?.ok ? p.name : ref.id,
    clock: {
      min: Math.min(...all.map(x => x.clock_from)), max: Math.max(...all.map(x => x.clock_to)), zero: clock.zero ?? "", reference: clock.reference,
      existing: p?.ok ? p.clock_existing : null, evidence: decisions?.clock?.evidence ?? {},
    },
    cameras, off: p?.ok ? p.cameras.map(c => c.name).filter(n => !cameras.some(c => c.key === n)) : [], flags, manual, needs,
  };
}

const camera = (key: string, kind: string, parts: Part[]): Camera => ({
  key, label: `${key}, ${kind === "depth" ? "depth camera" : `${kind} camera`}`, kind, sound: parts.some(x => x.sound), fps: parts[0]?.fps ?? 0, parts });

// One part of a session by camera and key, from cameras.json, else from clock.json.
export async function findPart(ref: SessionRef, cam: string, key: string) {
  const cams = await readCameras(ref), c = cams?.cameras.find(x => x.name === cam), q = c?.parts.find(x => x.key === key);
  if (c && q) return { cam: c.name, kind: c.kind, key: q.key, file: q.file, depth: q.depth_video ?? null, fps: q.fps, frames: q.frames, sound: !!q.sound, parts: c.parts.map(x => x.key) };
  const clock = await readJson<ClockJson>(work(ref, "clock", "clock.json")), cc = clock?.cameras[cam], qq = cc?.parts.find(x => x.key === key);
  if (cc && qq) return { cam, kind: cc.kind, key: qq.key, file: qq.file, depth: null, fps: qq.fps, frames: qq.frames, sound: cc.kind !== "depth", parts: cc.parts.map(x => x.key) };
  return null;
}
