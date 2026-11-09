// The transcripts of a session's videos, and the request guards. A video is named "<session>/<camera>/<part key>".
// The transcript step writes its cues into work/transcript/<camera>/<video stem>.srt; the person's edits are saved into
// transcripts/<camera>/<video stem>.srt with a rebuilt .txt, and transcripts/<camera>/.transcript_history/ keeps the
// replaced version, a copy of each saved one, and edits thrown away unsaved. Nothing is ever written beside a video.
import fsp from "node:fs/promises";
import path from "node:path";
import { formatSrt, formatTxt, normalizeCues, parseSrt, type Cue } from "@/lib/srt";
import type { CamerasJson, SessionRef } from "./camrig";

const HISTORY = ".transcript_history";

export class HttpError extends Error {
  status: number;
  extra?: Record<string, unknown>;
  constructor(status: number, message: string, extra?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.extra = extra;
  }
}

const missing = (e: unknown) => ["ENOENT", "ENOTDIR", "ENAMETOOLONG"].includes((e as NodeJS.ErrnoException)?.code ?? "");
export const statOrNull = (abs: string) => fsp.stat(abs).catch(e => (missing(e) ? null : Promise.reject(e)));

export type Video = { ref: string; session: string; dir: string; cam: string; key: string; abs: string; sound: boolean };

// The video a name points at, from the session's cameras.json or clock.json (imported lazily: camrig.ts imports this file).
export async function resolveVideo(ref: string): Promise<Video> {
  const [session, cam, key, extra] = ref.split("/");
  if (!session || !cam || !key || extra) throw new HttpError(400, "A video is named <session>/<camera>/<part>.");
  const { apiSession, findPart } = await import("./camrig");
  const s = await apiSession(session), p = await findPart(s, cam, key);
  if (!p || !(await statOrNull(p.file))?.isFile()) throw new HttpError(404, "No such video");
  return { ref, session, dir: s.dir, cam, key, abs: p.file, sound: p.sound };
}

const stemOf = (v: Video) => path.basename(v.abs, path.extname(v.abs));
export const editedPath = (v: Video) => path.join(v.dir, "transcripts", v.cam, `${stemOf(v)}.srt`);
export const stepPath = (v: Video) => path.join(v.dir, "work", "transcript", v.cam, `${stemOf(v)}.srt`);

// source: "edited" (the person's), "step" (the transcript step's own) or null (none yet).
// mtimeMs is the edited file's, null until the first save: a save is refused when the edited file changed since.
export type Transcript = { srt: string; exists: boolean; mtimeMs: number | null; cues: Cue[]; skipped: number; source: "edited" | "step" | null };

export async function readTranscript(ref: string): Promise<Transcript> {
  const v = await resolveVideo(ref), srt = `${stemOf(v)}.srt`;
  for (const [file, source] of [[editedPath(v), "edited"], [stepPath(v), "step"]] as const) {
    const fh = await fsp.open(file, "r").catch(e => (missing(e) ? null : Promise.reject(e)));
    if (!fh) continue;
    try {
      const [st, text] = await Promise.all([fh.stat(), fh.readFile("utf8")]);
      return { srt, exists: true, mtimeMs: source === "edited" ? st.mtimeMs : null, source, ...parseSrt(text) };
    } finally {
      await fh.close();
    }
  }
  return { srt, exists: false, mtimeMs: null, cues: [], skipped: 0, source: null };
}

// Per video file of the session: the cues of its transcript and whose it is, or nothing when it has none yet.
export async function transcriptCounts(ref: SessionRef, cams: CamerasJson): Promise<Record<string, { cues: number; source: "edited" | "step" }>> {
  const out: Record<string, { cues: number; source: "edited" | "step" }> = {};
  await Promise.all(cams.cameras.flatMap(c => c.parts.filter(q => q.sound).map(async q => {
    const v = { dir: ref.dir, cam: c.name, abs: q.file } as Video;
    for (const [file, source] of [[editedPath(v), "edited"], [stepPath(v), "step"]] as const) {
      const text = await fsp.readFile(file, "utf8").catch(() => null);
      if (text != null) { out[q.file] = { cues: text.split("-->").length - 1, source }; return; }
    }
  })));
  return out;
}

// Returns the file's modified time, read before the rename (which keeps it) so another writer cannot slip in between.
async function writeAtomic(abs: string, text: string): Promise<number> {
  const tmp = `${abs}.tmp-${crypto.randomUUID()}.part`;
  await fsp.writeFile(tmp, text, "utf8");
  const { mtimeMs } = await fsp.stat(tmp);
  await fsp.rename(tmp, abs);
  return mtimeMs;
}

const stampNow = () => {
  const d = new Date(), p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${p(d.getMilliseconds(), 3)}`;
};

// Writes transcripts/<camera>/<stem>.srt and its .txt. Refuses (409) when the edited file changed on disk since
// `baseMtimeMs` (null = it did not exist) unless `force`. The version being replaced (the edited one, else the step's)
// is copied to .transcript_history/ first, and the new one is kept there too as <stem>.<time>.saved.srt.
export async function saveTranscript(ref: string, cues: Cue[], baseMtimeMs: number | null, force = false) {
  const v = await resolveVideo(ref), clean = normalizeCues(cues);
  if ("error" in clean) throw new HttpError(400, clean.error);
  const srtAbs = editedPath(v), txtAbs = srtAbs.replace(/\.srt$/, ".txt"), current = await statOrNull(srtAbs);
  if (!force && (current?.mtimeMs ?? null) !== baseMtimeMs) throw new HttpError(409, "The transcript changed on disk since it was opened.", { mtimeMs: current?.mtimeMs ?? null });
  const dir = path.join(path.dirname(srtAbs), HISTORY), stem = path.basename(srtAbs, ".srt"), stamp = stampNow(), text = formatSrt(clean.cues);
  await fsp.mkdir(dir, { recursive: true });
  const replaced = current ? srtAbs : (await statOrNull(stepPath(v))) ? stepPath(v) : null;
  let backup: string | null = null;
  if (replaced) {
    backup = path.join(dir, `${stem}.${stamp}.srt`);
    await fsp.copyFile(replaced, backup);
  }
  if (current) await fsp.copyFile(txtAbs, path.join(dir, `${stem}.${stamp}.txt`)).catch(e => (missing(e) ? undefined : Promise.reject(e)));
  await writeAtomic(path.join(dir, `${stem}.${stamp}.saved.srt`), text);
  const mtimeMs = await writeAtomic(srtAbs, text);
  await writeAtomic(txtAbs, formatTxt(clean.cues));
  return { srt: path.relative(v.dir, srtAbs), mtimeMs, count: clean.cues.length, backup: backup && path.relative(v.dir, backup) };
}

// Keeps edits the person is about to throw away (to load the disk version instead) as
// .transcript_history/<stem>.<time>.unsaved.srt; the transcript itself is not touched. Every cue with words is kept:
// a missing or reversed time is clamped instead of refused.
export async function stashTranscript(ref: string, cues: Cue[]) {
  const v = await resolveVideo(ref);
  const clean = normalizeCues(cues.filter(c => String(c.text ?? "").trim()).map(c => {
    const startMs = Math.max(0, Math.round(c.startMs) || 0);
    return { startMs, endMs: Math.max(Math.round(c.endMs) || 0, startMs + 1), text: c.text };
  }));
  if ("error" in clean) throw new HttpError(400, clean.error);
  const srtAbs = editedPath(v), dir = path.join(path.dirname(srtAbs), HISTORY);
  const file = path.join(dir, `${path.basename(srtAbs, ".srt")}.${stampNow()}.unsaved.srt`);
  await fsp.mkdir(dir, { recursive: true });
  await writeAtomic(file, formatSrt(clean.cues));
  return { backup: path.relative(v.dir, file), count: clean.cues.length };
}

// Requests must name this machine's loopback address as their Host (keeps DNS rebinding away from the files), and a
// request that writes must also carry no Origin or this app's own.
export const localHost = (req: Request) => /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(req.headers.get("host") ?? "");

// A read must come from this app's own pages, a link the person followed, or outside a browser: a page on another site
// could otherwise embed a session's files in its own.
export function readAllowed(req: Request): boolean {
  const site = req.headers.get("sec-fetch-site"), h = (name: string) => req.headers.get(name);
  return localHost(req) && (!site || site === "same-origin" || site === "none" || (h("sec-fetch-mode") === "navigate" && h("sec-fetch-dest") === "document"));
}

export function fromThisApp(req: Request): boolean {
  if (!localHost(req)) return false;
  const origin = req.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === req.headers.get("host");
  } catch {
    return false;
  }
}

// CAMRIG_READONLY: the sessions are only shown; no route that writes or runs answers.
export const READ_ONLY = !!process.env.CAMRIG_READONLY;

// Route handlers: the answer as JSON, an HttpError as its status and message.
export async function answer(req: Request, fn: () => Promise<unknown>, write = false): Promise<Response> {
  const noStore = { "Cache-Control": "no-store" };
  if (!(write ? fromThisApp(req) : fromThisApp(req) && readAllowed(req))) return Response.json({ error: "Forbidden" }, { status: 403 });
  if (write && READ_ONLY) return Response.json({ error: "This app only shows the sessions: nothing here can be run, saved or made. "
    + "To change a session, start the app without CAMRIG_READONLY, on a copy." }, { status: 403, headers: noStore });
  try {
    return Response.json(await fn(), { headers: noStore });
  } catch (e) {
    if (e instanceof HttpError) return Response.json({ error: e.message, ...e.extra }, { status: e.status, headers: noStore });
    throw e;
  }
}
