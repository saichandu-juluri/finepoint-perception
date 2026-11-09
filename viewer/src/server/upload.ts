// The upload lane (docs/UPLOAD.md): `python -m camrig.upload <project.toml>` in the core environment sends a
// session's files to the cloud. It is not under the one-job lock: it runs beside the steps, detached, so closing the
// page or stopping this server does not stop it. It writes upload/progress.json about once a second; Pause is the file
// upload/pause.request, which the uploader deletes once it has paused. When this computer is signed in, the uploader is started
// again after a run of the session ends, after a Save in Multi-view, and, when this server starts, for a session whose
// progress says running (the computer was restarted). No start is decided on progress.json's pid: after a restart that
// pid may belong to an unrelated process. The uploader keeps itself to one per session (an OS lock, one waiting after
// it, any further start ends at once), so starting it whenever a pass is wanted is safe.
// The lab's token is never read here: the uploader and cloud_account take it from the password safe themselves.
import { spawn } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PY, REPO, checkPython, listSessions, readJson, type SessionRef } from "./camrig";
import { HttpError, READ_ONLY } from "./data";

export type UploadFile = { path: string; kind: string; size: number; bytes_done: number; parts_done: number; parts_total: number; status: string; error: string };
export type Progress = { pid: number; state: string; started: string; updated: string; bytes_total: number; bytes_done: number; rate_bps: number;
  eta_s: number | null; error: string; files: UploadFile[] };
export type CloudList = { files: { path: string; size: number; sha256: string; kind: string; modified: string }[]; uploading: { path: string; started: string }[] };
// state: progress.json's, or "none" (never started), "starting" (spawned, no progress of its own yet), "stopped" (says
// running but its process is gone: the next start of this server, or Start, resumes it)
export type UploadState = { cloud: boolean; account: CloudStatus; state: string; progress: Progress | null; exit: string | null; inCloud: CloudList | null; cloudError: string | null };

const SESSION = /^[a-z0-9_-]{1,64}$/; // the cloud's session name, as camrig/upload.py and cloud/api.py check it
// The last uploader this server started, by session: when, and how it ended.
const kids = ((globalThis as typeof globalThis & { camrigUploads?: Map<string, Kid> }).camrigUploads ??= new Map());
type Kid = { at: number; code: number | null; out: string };

// This computer's sign-in, as `python -m camrig.cloud_account status` gives it (never the token: the uploader and
// cloud_account read that themselves). Asked once, and again after a sign-in or sign-out here.
export type CloudStatus = { url: string | null; signedIn: boolean; email: string | null; name: string | null; lab: string | null; fromEnv: boolean };
const NONE: CloudStatus = { url: null, signedIn: false, email: null, name: null, lab: null, fromEnv: false };
const memo = globalThis as typeof globalThis & { camrigCloud?: Promise<CloudStatus> };

export function cloudAccount(args: string[], stdin = ""): Promise<any> {
  checkPython();
  return new Promise((ok, no) => {
    const child = spawn(PY, ["-m", "camrig.cloud_account", ...args], { cwd: REPO, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, timeout: 45_000 });
    let out = "";
    child.stdout.on("data", d => (out += d));
    child.on("error", e => no(new HttpError(500, `The cloud sign-in could not be asked: ${e.message}`)));
    child.on("close", code => {
      let j: any = null;
      try { j = JSON.parse(out); } catch {}
      if (code === 0 && j && !j.error) return ok(j);
      const msg = String(j?.error ?? `the cloud sign-in stopped (code ${code})`);
      no(new HttpError(400, msg[0].toUpperCase() + msg.slice(1)));
    });
    child.stdin.end(stdin);
  });
}

export function cloudStatus(fresh = false): Promise<CloudStatus> {
  if (fresh || !memo.camrigCloud) {
    const asked = (memo.camrigCloud = cloudAccount(["status"]).then(s => ({ ...NONE, ...s }), e => {
      if (memo.camrigCloud === asked) memo.camrigCloud = undefined;
      console.warn(`FinePoint Perception: the cloud sign-in could not be read: ${(e as Error).message}`);
      return NONE;
    }));
  }
  return memo.camrigCloud!;
}

export const signedIn = async () => !!(process.env.CAMRIG_CLOUD_URL && process.env.CAMRIG_CLOUD_KEY) || (await cloudStatus()).signedIn;

const alive = (pid: unknown) => {
  if (typeof pid !== "number" || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return (e as NodeJS.ErrnoException).code === "EPERM"; }
};
const folder = (ref: SessionRef, f = "") => path.join(ref.dir, "upload", f);
const readProgress = (ref: SessionRef) => readJson<Progress>(folder(ref, "progress.json"));
const paused = (ref: SessionRef) => fs.existsSync(folder(ref, "pause.request"));
const busy = (kid: Kid | undefined) => !!kid && kid.code == null;
// progress.json written after this kid started (its time is to the second, so the second after): by it, or by the
// uploader it waits for
const newer = (p: Progress | null, kid: Kid) => !!p && Date.parse(p.updated) > kid.at;

async function inCloud(ref: SessionRef): Promise<Pick<UploadState, "inCloud" | "cloudError">> {
  if (!SESSION.test(ref.id) || !(await signedIn())) return { inCloud: null, cloudError: null };
  try {
    return { inCloud: (await cloudAccount(["files", ref.id])) as CloudList, cloudError: null };
  } catch (e) {
    return { inCloud: null, cloudError: (e as Error).message };
  }
}

export async function uploadState(ref: SessionRef, withCloud = true): Promise<UploadState> {
  const [progress, kid] = [await readProgress(ref), kids.get(ref.id)];
  let state = progress?.state ?? "none";
  if (busy(kid) && !newer(progress, kid!)) state = "starting";
  else if ((state === "running" || state === "pausing") && !alive(progress!.pid)) state = state === "pausing" ? "paused" : "stopped";
  const exit = kid && kid.code != null && kid.code !== 0 && !newer(progress, kid)
    ? `The uploader stopped at once (code ${kid.code}): ${kid.out || `no reason given; run it in a terminal to see why: pixi run --frozen -e core upload ${ref.toml}`}` : null;
  const settled = !["starting", "running", "pausing"].includes(state);
  return { cloud: await signedIn(), account: await cloudStatus(), state, progress, exit, ...(withCloud && settled ? await inCloud(ref) : { inCloud: null, cloudError: null }) };
}

// Its output goes to a file, not a pipe: a pipe would break the uploader once this server stops. The file is read when the
// uploader ends without a progress.json of its own (the project refused, say), and then deleted.
function spawnUploader(ref: SessionRef) {
  checkPython();
  const out = path.join(os.tmpdir(), `camrig-upload-${ref.id}-${Date.now()}.txt`), fd = fs.openSync(out, "w");
  let child;
  try {
    child = spawn(PY, ["-m", "camrig.upload", ref.toml], { cwd: REPO, detached: true, stdio: ["ignore", fd, fd], windowsHide: true });
  } finally {
    fs.closeSync(fd);
  }
  const kid: Kid = { at: Date.now(), code: null, out: "" };
  kids.set(ref.id, kid);
  child.on("error", () => { kid.code = -1; });
  child.on("exit", code => {
    try { kid.out = fs.readFileSync(out, "utf8").trim().slice(-600); } catch {}
    fs.rmSync(out, { force: true });
    kid.code = code ?? -1;
  });
  child.unref();
}

export async function startUpload(ref: SessionRef): Promise<UploadState> {
  if (!SESSION.test(ref.id)) throw new HttpError(400, `The session folder's name "${ref.id}" names the session in the cloud, so it must be lowercase letters, digits, _ and - only. Rename the folder, then start again.`);
  // an uploader started now would wait for the pausing one, then see paused and end: the Resume would be lost
  if (busy(kids.get(ref.id)) && (await readProgress(ref))?.state === "pausing")
    throw new HttpError(409, "The upload is still pausing: the pieces in flight finish first. Start it again once it says paused.");
  await fsp.rm(folder(ref, "pause.request"), { force: true }); // a pause left over would stop the new uploader at once
  spawnUploader(ref);
  return uploadState(ref, false);
}

export async function pauseUpload(ref: SessionRef): Promise<UploadState> {
  const st = await uploadState(ref, false);
  if (["starting", "running"].includes(st.state)) {
    await fsp.mkdir(folder(ref), { recursive: true });
    await fsp.writeFile(folder(ref, "pause.request"), `${new Date().toISOString()} paused in the app\n`);
  }
  return uploadState(ref, false);
}

// After something in the session changed: the uploader sends it, when this computer is signed in and the session's upload was
// started before. A pause the person asked for is kept. An uploader still running may already be past the changed file:
// this one waits for it, then makes its own pass.
export async function restartUpload(ref: SessionRef): Promise<void> {
  try {
    if (READ_ONLY || !(await signedIn())) return;
    const state = (await readProgress(ref))?.state;
    if (!state || state === "paused" || state === "pausing" || paused(ref)) return;
    spawnUploader(ref);
  } catch (e) {
    console.warn(`FinePoint Perception: the upload of ${ref.id} could not be started again: ${(e as Error).message}`);
  }
}

// On this server's start: every session whose progress says running. Its uploader was cut off, or it still runs (this
// server alone was restarted) and the one started here ends at once or waits for it.
export async function resumeUploads(): Promise<void> {
  if (READ_ONLY || !(await signedIn())) return;
  for (const ref of await listSessions().catch(() => [])) {
    if ((await readProgress(ref))?.state === "running") await restartUpload(ref);
  }
}

// The library card's word for a session's upload, or null when it was never started.
export async function uploadBadge(ref: SessionRef): Promise<{ text: string; tone: "ok" | "warn" | "bad" | "info" } | null> {
  const st = await uploadState(ref, false), p = st.progress;
  const pct = p?.bytes_total ? Math.floor((100 * p.bytes_done) / p.bytes_total) : 0;
  switch (st.state) {
    case "starting": case "running": return { text: `Uploading ${pct}%`, tone: "info" };
    case "pausing": case "paused": return { text: `Upload paused at ${pct}%`, tone: "warn" };
    case "stopped": return { text: `Upload cut off at ${pct}%`, tone: "warn" };
    case "done": return { text: "In the cloud", tone: "ok" };
    case "failed": return { text: "Upload failed", tone: "bad" };
    case "waiting_for_cloud": return { text: "Waiting for a cloud", tone: "warn" };
    default: return null;
  }
}
