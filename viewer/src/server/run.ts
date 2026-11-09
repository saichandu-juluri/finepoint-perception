// A run of a session's steps, `python -m camrig.run <project.toml> [--only step]` in the core environment, on the
// server on its own: POST starts it, GET reports, DELETE cancels (Ctrl-C to the runner and the step it started). One job
// at a time on this server. Each step's state comes from its work/<step>/check.json: the runner deletes it as the step
// starts and writes it as the step ends, and writes log.txt meanwhile. When the run ends, the session's uploader is
// started again, so what the run changed goes up.
import { spawn, spawnSync } from "node:child_process";
import fsp from "node:fs/promises";
import { type Item, PY, REPO, checkPython, project, readCheck, steps, work, type SessionRef } from "./camrig";
import { HttpError, statOrNull } from "./data";
import { restartUpload } from "./upload";

// Windows has no process groups to signal, and no Ctrl-C for a process without a console of its own: taskkill /T /F ends
// the runner and every process under it at once. A step stopped so leaves no check.json, so the next run starts it again.
const WINDOWS = process.platform === "win32";
const killTree = (pid: number) => spawnSync("taskkill", ["/T", "/F", "/PID", String(pid)], { stdio: "ignore", windowsHide: true });

type Job = { session: string; only: string | null; started: number; ended: number | null; status: "running" | "done" | "failed" | "cancelled";
  error: string | null; output: string; skipped: string[]; abort: AbortController };
export type StepState = {
  name: string; status: string | null; started: string | null; seconds: number | null; items: Item[]; log: string[];
  now: "running" | "waiting" | "ran" | "skipped" | null; // during or after this app's last run of the session
  stale: string[]; // the steps it builds on that ran after it, so its result may rest on their older one
};
export type RunState = { busy: boolean; job: Omit<Job, "abort"> | null; steps: StepState[]; now: number };

// On globalThis: it survives a hot reload and leaving the page, and the exit hook below sees the runner. next dev
// restarts its worker with process.exit and no request closed, which would leave the runner and its step behind.
const state = globalThis as typeof globalThis & { camrigRun?: Job; camrigBusy?: boolean; camrigPid?: number; camrigExitHook?: boolean };
if (!state.camrigExitHook) {
  state.camrigExitHook = true;
  // SIGTERM, not SIGKILL: the runner then stops its step, which runs in a process group of its own
  process.once("exit", () => {
    if (state.camrigPid && WINDOWS) killTree(state.camrigPid);
    else if (state.camrigPid) try { process.kill(-state.camrigPid, "SIGTERM"); } catch {}
  });
}

// One job at a time on this server: the Mac runs one well. The lock is taken as exclusive() is called (before its first
// await), so busy() and then exclusive() in one go cannot race.
// ponytail: one lock for the whole server; a queue if several people start jobs
const BUSY = "Another run of the steps is going on. Wait for it, or cancel it, then try again.";
const busy = () => !!state.camrigBusy;
async function exclusive<T>(fn: () => Promise<T>): Promise<T> {
  if (state.camrigBusy) throw new HttpError(409, BUSY);
  state.camrigBusy = true;
  try { return await fn(); } finally { state.camrigBusy = false; }
}

// `output`: the end of stdout and stderr together.
class ToolError extends Error {
  constructor(tool: string, why: string, public output: string) { super(`${tool} ${why}`); }
}

// One tool in its own process group, so a cancel, a timeout or the server's exit also stops what it started (the runner
// starts a step). A cancel or a timeout sends Ctrl-C first, so a Python step can tidy up its temporary files, and kills after 3 s.
// On Windows the tool gets a console of its own with no window, which its steps and their ffmpeg share, so none of them
// opens a window; a cancel ends the tree at once (killTree).
function run(name: string, cmd: string, args: string[], o: { signal: AbortSignal; timeoutMs: number; env?: NodeJS.ProcessEnv; cwd?: string; onData?: (text: string) => void }): Promise<void> {
  return new Promise((resolve, reject) => {
    if (o.signal.aborted) return reject(new ToolError(name, "was cancelled", ""));
    const child = spawn(cmd, args, { detached: !WINDOWS, windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: o.env, cwd: o.cwd });
    state.camrigPid = child.pid;
    let tail = "", why = "", done = false, kill: NodeJS.Timeout | undefined;
    const group = (sig: NodeJS.Signals) => {
      if (WINDOWS) { if (child.pid) killTree(child.pid); return; }
      try { process.kill(-child.pid!, sig); } catch {}
    };
    const stop = (reason: string) => { if (why) return; why = reason; group("SIGINT"); kill = setTimeout(() => group("SIGKILL"), 3000); };
    const onAbort = () => stop("was cancelled");
    const timer = setTimeout(() => stop(`took longer than ${o.timeoutMs / 60_000} min`), o.timeoutMs);
    o.signal.addEventListener("abort", onAbort, { once: true });
    child.stdout!.on("data", d => { tail = (tail + d).slice(-4000); o.onData?.(String(d)); });
    child.stderr!.on("data", d => { tail = (tail + d).slice(-4000); o.onData?.(String(d)); });
    const end = (e: ToolError | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      clearTimeout(kill);
      o.signal.removeEventListener("abort", onAbort);
      if (state.camrigPid === child.pid) state.camrigPid = undefined;
      if (e) reject(e); else resolve();
    };
    child.on("error", e => end(new ToolError(name, `could not start (${e.message})`, tail)));
    child.on("close", code => end(code === 0 && !why ? null : new ToolError(name, why || `stopped with code ${code}`, tail)));
  });
}

// What a failed run tells the page: the tool and the end of what it printed.
function failure(e: unknown): string {
  if (!(e instanceof ToolError)) return `It failed: ${(e as Error).message}`;
  // A Python traceback ends in one line that names the error; other output keeps its end.
  const lines = e.output.split("\n").map(l => l.trim()).filter(Boolean);
  const tail = e.output.includes("Traceback (most recent call last)") ? lines.at(-1) ?? "" : lines.join(" ").slice(-500);
  return `${e.message}${tail ? `: ${tail}` : "."}`;
}

async function tail(file: string, n = 12): Promise<string[]> {
  const text = await fsp.readFile(file, "utf8").catch(() => "");
  return text.split(/\r|\n/).map(l => l.trimEnd()).filter(Boolean).slice(-n);
}

export async function runState(ref: SessionRef): Promise<RunState> {
  const j = state.camrigRun?.session === ref.id ? state.camrigRun : null, names = await steps();
  const out: StepState[] = await Promise.all(names.map(async name => {
    const [check, st, log] = await Promise.all([readCheck(ref, name), statOrNull(work(ref, name, "check.json")), tail(work(ref, name, "log.txt"))]);
    let now: StepState["now"] = null;
    if (j && (!j.only || j.only === name)) {
      if (j.skipped.includes(name)) now = "skipped";
      else if (st && st.mtimeMs >= j.started) now = "ran";
      else if (j.status === "running") now = st ? "waiting" : "running";
    }
    return { name, status: check?.status ?? null, started: check?.started ?? null, seconds: check?.seconds ?? null, items: check?.items ?? [], log, now, stale: [] };
  }));
  // the project's own needs, as the runner sees them. In run order, so a step that builds on an out-of-date step is out
  // of date too.
  const p = await project(ref), byName = Object.fromEntries(out.map(s => [s.name, s]));
  const deps = (name: string) => (p.ok ? p.needs[name] ?? [] : []);
  for (const s of out) if (s.started) s.stale = deps(s.name).filter(n => (byName[n]?.started ?? "") > s.started! || byName[n]?.stale.length);
  // Steps the runner has not reached yet still hold last time's check.json; only the first without one is running.
  const first = out.findIndex(s => s.now === "running");
  out.forEach((s, i) => { if (s.now === "running" && i !== first) s.now = "waiting"; });
  return { busy: busy(), job: j && { ...j, abort: undefined } as Omit<Job, "abort">, steps: out, now: Date.now() };
}

export const running = (id: string) => state.camrigRun?.session === id && state.camrigRun.status === "running";

export async function startRun(ref: SessionRef, only: string | null): Promise<RunState> {
  if (only && !(await steps()).includes(only)) throw new HttpError(400, `There is no step called ${only}.`);
  checkPython();
  if (busy()) throw new HttpError(409, BUSY);
  const job: Job = { session: ref.id, only, started: Date.now(), ended: null, status: "running", error: null, output: "", skipped: [], abort: new AbortController() };
  state.camrigRun = job;
  const onData = (d: string) => {
    job.output = (job.output + d).slice(-20_000);
    for (const m of d.matchAll(/^(\w+)\s+.*\(unchanged, skipped\)/gm)) job.skipped.push(m[1]);
  };
  exclusive(() => run("the runner", PY, ["-u", "-m", "camrig.run", ref.toml, ...(only ? ["--only", only] : [])],
    { signal: job.abort.signal, timeoutMs: 48 * 3600_000, cwd: REPO, env: { ...process.env, PYTHONUNBUFFERED: "1" }, onData }))
    .then(() => { job.status = "done"; })
    .catch(e => {
      // The runner exits 1 when a step failed: that is a finished run whose step says why, not a broken one.
      if (e instanceof ToolError && /stopped with code 1$/.test(e.message) && !job.abort.signal.aborted && /^\w+\s+FAIL/m.test(job.output)) { job.status = "done"; return; }
      job.status = job.abort.signal.aborted ? "cancelled" : "failed";
      job.error = job.status === "failed" ? failure(e) : null;
    })
    .finally(() => { job.ended = Date.now(); restartUpload(ref); });
  return runState(ref);
}

export async function cancelRun(ref: SessionRef): Promise<RunState> {
  if (!running(ref.id)) throw new HttpError(409, "No run started here is going on for this session.");
  state.camrigRun!.abort.abort();
  return runState(ref);
}
