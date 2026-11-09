// New project: a folder on this computer with one subfolder per camera becomes a session. The folder is browsed here on
// the server (the browser cannot see the disk). A subfolder with .bag recordings is the depth camera, one with videos an
// action camera. The project file holds the names and board the person confirmed, goes to
// <first sessions folder>/<name>/project.toml (never among the recordings), and is checked by camrig's own reader
// before the page says it is written.
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { projects, REPO, ROOTS } from "./camrig";
import { HttpError, statOrNull } from "./data";

const VIDEO = new Set([".mov", ".mp4", ".m4v", ".avi", ".mkv"]); // camrig/project.py VIDEO_EXT

// From PATH, which pixi provides, else the core environment; `env` names the setting that points at another program.
function tool(name: string, env: string): string {
  const set = process.env[env];
  const dirs = [...(process.env.PATH ?? "").split(path.delimiter), path.join(REPO, ".pixi", "envs", "core", "bin")];
  for (const p of set ? [set] : dirs.filter(Boolean).map(d => path.join(d, name))) {
    try { fs.accessSync(p, fs.constants.X_OK); return p; } catch {}
  }
  throw new HttpError(503, set ? `${env}=${set} is not a program that can run; fix the path or leave ${env} unset.`
    : `${name} was not found. Install it (pixi install -e core --locked in the app's folder, then start the app through pixi) or start the app with ${env}=/path/to/${name}.`);
}

export type CameraFolder = { name: string; kind: "action" | "depth"; folder: string; files: number; seconds: number | null; bytes: number; first: string | null };
export type Browse = { path: string; ok: boolean; error: string | null; parent: string | null; dirs: { name: string; path: string }[]; cameras: CameraFolder[] };

const probe = (file: string) => new Promise<number | null>(ok => execFile(tool("ffprobe", "CAMRIG_FFPROBE"), ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file],
  { timeout: 20_000 }, (e, out) => ok(e ? null : Number(out.trim()) || null)));

async function cameraFolder(dir: string): Promise<CameraFolder | null> {
  const files = (await fsp.readdir(dir, { withFileTypes: true }).catch(() => [])).filter(e => e.isFile() && !e.name.startsWith(".")).map(e => e.name).sort();
  const bags = files.filter(f => path.extname(f).toLowerCase() === ".bag"), videos = files.filter(f => VIDEO.has(path.extname(f).toLowerCase()));
  const mine = bags.length ? bags : videos;
  if (!mine.length) return null;
  const sizes = await Promise.all(mine.map(f => statOrNull(path.join(dir, f))));
  // ponytail: lengths only for a folder of up to 40 videos (a camera's 4 GB parts); a folder like Downloads is not probed
  const lengths = bags.length || videos.length > 40 ? [] : await Promise.all(videos.map(f => probe(path.join(dir, f))));
  return {
    name: path.basename(dir).replace(/ /g, "_"), kind: bags.length ? "depth" : "action", folder: dir, files: mine.length,
    seconds: lengths.length && lengths.every(l => l != null) ? lengths.reduce((a, b) => a + b!, 0) : null,
    bytes: sizes.reduce((n, s) => n + (s?.size ?? 0), 0), first: bags.length ? null : path.join(dir, videos[0]),
  };
}

// The folder as typed: whether it can be used, its subfolders to browse into, and the cameras it would give.
export async function browse(typed: string | null): Promise<Browse> {
  const p = path.resolve((typed || os.homedir()).replace(/^~(?=$|\/)/, os.homedir()));
  const st = await statOrNull(p);
  if (!st?.isDirectory()) return { path: p, ok: false, error: st ? "This is a file, not a folder." : "There is no folder here.", parent: path.dirname(p), dirs: [], cameras: [] };
  const entries = await fsp.readdir(p, { withFileTypes: true }).catch(() => null);
  if (!entries) return { path: p, ok: false, error: "This folder cannot be read.", parent: path.dirname(p), dirs: [], cameras: [] };
  const dirs = entries.filter(e => e.isDirectory() && !e.name.startsWith(".")).map(e => ({ name: e.name, path: path.join(p, e.name) })).sort((a, b) => a.name.localeCompare(b.name));
  const cameras = (await Promise.all(dirs.map(d => cameraFolder(d.path)))).filter((c): c is CameraFolder => !!c);
  return {
    path: p, parent: p === path.dirname(p) ? null : path.dirname(p), dirs, cameras,
    ok: cameras.length >= 2, error: cameras.length >= 2 ? null : `A session needs one subfolder per camera, at least two with videos or .bag recordings; this folder has ${cameras.length}.`,
  };
}

// One picture from a video, 1 s in (or its first frame), as JPEG.
export function picture(file: string): Promise<Buffer> {
  if (!VIDEO.has(path.extname(file).toLowerCase())) return Promise.reject(new HttpError(400, "Not a video"));
  const grab = (at: string) => new Promise<Buffer>((ok, no) => execFile(tool("ffmpeg", "CAMRIG_FFMPEG"),
    ["-nostdin", "-v", "error", "-ss", at, "-i", file, "-frames:v", "1", "-vf", "scale=480:-2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"],
    { encoding: "buffer", timeout: 30_000, maxBuffer: 1 << 24 }, (e, out) => (e || !out.length ? no(new HttpError(422, "No picture could be taken from this video.")) : ok(out))));
  return grab("1").catch(() => grab("0"));
}

export type NewProject = { folder: string; name: string; board: { columns: number; rows: number; square_mm: number; marker_mm: number }; cameras: { folder: string; name: string }[] };

const q = (s: string) => JSON.stringify(s); // a TOML basic string

// The project file, with the person's names and board.
function text(p: NewProject, kinds: Map<string, string>): string {
  const b = p.board;
  let s = `name = ${q(p.name)}\n\n# The board shown to the cameras, as the person who made this project measured it.\n[board]\n`
    + `squares = [${b.columns}, ${b.rows}]                     # columns, rows of squares\nsquare_mm = ${b.square_mm}                     # one square's side\nmarker_mm = ${b.marker_mm}                     # one marker's side\n`;
  for (const c of p.cameras) {
    s += `\n[[camera]]\nname = ${q(c.name)}                       # the label on the camera body\nkind = ${q(kinds.get(c.folder)!)}\nfolder = ${q(c.folder)}\n`;
    if (kinds.get(c.folder) === "depth") s += 'files = "*.bag"\n';
  }
  return s;
}

export async function create(p: NewProject): Promise<{ id: string; toml: string }> {
  const name = String(p?.name ?? "").trim(), id = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!id) throw new HttpError(400, "Give the session a name.");
  const b = p.board, nums = [b?.columns, b?.rows, b?.square_mm, b?.marker_mm];
  if (!nums.every(n => typeof n === "number" && Number.isFinite(n) && n > 0) || !Number.isInteger(b.columns) || !Number.isInteger(b.rows) || b.marker_mm >= b.square_mm)
    throw new HttpError(400, "The board needs whole numbers of columns and rows, and a marker smaller than its square, in mm.");
  const found = await browse(p.folder);
  if (!found.ok) throw new HttpError(400, found.error ?? "This folder cannot be used.");
  const kinds = new Map(found.cameras.map(c => [c.folder, c.kind]));
  const names = (p.cameras ?? []).map(c => String(c?.name ?? "").trim());
  if (!p.cameras?.length || p.cameras.some(c => !kinds.has(c?.folder))) throw new HttpError(400, "Choose cameras from the subfolders of this folder.");
  if (p.cameras.length < 2) throw new HttpError(400, "A session needs at least two cameras.");
  if (names.some(n => !/^[A-Za-z0-9_-]+$/.test(n))) throw new HttpError(400, "Camera names are short labels of letters, digits, - and _, such as cam1.");
  if (new Set(names).size !== names.length) throw new HttpError(400, "Two cameras have the same name.");
  if (await statOrNull(path.join(ROOTS[0], "project.toml")))
    throw new HttpError(409, `${ROOTS[0]} is a session itself, so a new project has no sessions folder to go in. Start the app with `
      + "CAMRIG_SESSIONS naming a folder that holds session folders first.");
  const dir = path.join(ROOTS[0], id), toml = path.join(dir, "project.toml");
  if (await statOrNull(toml)) throw new HttpError(409, `A session called ${id} exists already. Choose another name.`);
  const made = await fsp.mkdir(dir, { recursive: true });
  await fsp.writeFile(toml, text({ ...p, name, cameras: p.cameras.map((c, i) => ({ folder: c.folder, name: names[i] })) }, kinds), { flag: "wx" });
  const r = (await projects([toml]))[toml];
  if (!r.ok) {
    await fsp.unlink(toml);
    if (made) await fsp.rmdir(dir).catch(() => {}); // only a folder made here, and only while empty
    throw new HttpError(422, `The project file was refused: ${r.error}`);
  }
  return { id, toml };
}
