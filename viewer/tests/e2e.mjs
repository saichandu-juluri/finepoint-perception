// The app end to end, in headless Chrome over the DevTools protocol:  npm run e2e   (next build, then this)
// Everything happens in a scratch folder (E2E_TMP, default the system temp folder): short clips made with ffmpeg, the
// sessions folders, a home folder and a test password safe, deleted at the end. Three servers of the app run on free ports:
//   A, not signed in: the library, New project from the clips, Run, the single view and its transcript, Multi-view and
//     the guards;
//   B, signed in for a demo (CAMRIG_CLOUD_URL and CAMRIG_CLOUD_KEY), with the stand-in for the cloud slowed down
//     (tests/slow_cloud.py): an upload a restart cut off, Upload with Pause and Resume, sync by hand, stopping uploaders;
//   C, not signed in, with the stand-in for the cloud (cloud/dev_server.py): the sign-in form, an upload, Sign out.
// It ends by checking that the clips and the real ~/.camrig are unchanged, and prints PASS or FAIL.
// Env: E2E_TMP (scratch folder), E2E_SHOTS (folder for screenshots), CHROME (the Chrome program; on Windows the default
// is Chrome, else Edge).
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseSrt } from "../src/lib/srt.ts";

const WIN = process.platform === "win32";
const ROOT = fileURLToPath(new URL("..", import.meta.url)).replace(/[\\/]$/, ""), REPO = path.dirname(ROOT), ENV = path.join(REPO, ".pixi", "envs", "core");
// The core environment's Python, and the folder of its ffmpeg: on Windows python.exe at its top, ffmpeg.exe in Library\bin
const PY = WIN ? path.join(ENV, "python.exe") : path.join(ENV, "bin", "python"), BIN = WIN ? path.join(ENV, "Library", "bin") : path.dirname(PY);
const PY_UTF8 = WIN ? ["-X", "utf8"] : [];           // -I ignores PYTHONUTF8 (camrig.ts PY_UTF8)
const NEXT = path.join(ROOT, "node_modules", "next", "dist", "bin", "next");
const programs = [process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA].filter(Boolean);
const CHROME = process.env.CHROME ?? (!WIN ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  : [...programs.map(p => path.join(p, "Google", "Chrome", "Application", "chrome.exe")), ...programs.map(p => path.join(p, "Microsoft", "Edge", "Application", "msedge.exe"))].find(f => fs.existsSync(f)));
const SHOTS = process.env.E2E_SHOTS ? path.resolve(process.env.E2E_SHOTS) : null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failed = 0;
const check = (ok, what) => {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${what}`);
  if (!ok) failed++;
};

// ---------- scratch, sandbox and clips ----------
const scratch = fs.mkdtempSync(path.join(process.env.E2E_TMP || os.tmpdir(), "fpp-e2e-"));
const HOME = path.join(scratch, "home"), SAFE = path.join(scratch, "safe"), TMP = path.join(scratch, "tmp"), REAL_CAMRIG = path.join(os.homedir(), ".camrig");
for (const d of [HOME, SAFE, TMP]) fs.mkdirSync(d);
// Every program started here gets a temporary home folder, a test password safe and the scratch folder for its
// temporary files: never the real ~/.camrig or password safe (Windows: USERPROFILE, TEMP and TMP; Credential Manager).
// No cloud unless a server is given one. Windows names PATH as it likes (Path): one key only, or the child gets either.
const pathKey = Object.keys(process.env).find(k => k.toUpperCase() === "PATH") ?? "PATH";
const sandbox = { ...process.env, HOME, TMPDIR: TMP, ...(WIN ? { USERPROFILE: HOME, TEMP: TMP, TMP } : {}),
  PYTHON_KEYRING_BACKEND: "camrig.keyring_file.FileKeyring", CAMRIG_TEST_KEYRING_DIR: SAFE, CAMRIG_CLOUD_URL: "", CAMRIG_CLOUD_KEY: "",
  [pathKey]: [...(WIN ? [ENV] : []), BIN, process.env[pathKey]].join(path.delimiter) };
const realCamrig = () => (fs.existsSync(REAL_CAMRIG) ? fs.readdirSync(REAL_CAMRIG).map(f => `${f}:${fs.statSync(path.join(REAL_CAMRIG, f)).mtimeMs}`).join("|") : null);
const realCamrigBefore = realCamrig();

// A 3 s clip of a test picture, with a tone of `hz` or silent.
const clip = (file, hz) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const r = spawnSync(path.join(BIN, WIN ? "ffmpeg.exe" : "ffmpeg"), ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3",
    ...(hz ? ["-f", "lavfi", "-i", `sine=frequency=${hz}:duration=3`, "-shortest", "-c:a", "aac"] : []), "-c:v", "libx264", "-pix_fmt", "yuv420p", file]);
  if (r.status !== 0) throw new Error(`ffmpeg: ${r.stderr}`);
};
// New project's recordings: camA with sound in both clips, camB silent. With one camera of sound the clock step decodes
// no sound, so the three steps run in seconds.
const CLIPS = path.join(scratch, "e2e clips");
for (const n of [1, 2]) { clip(path.join(CLIPS, "camA", `camA_clip${n}.mp4`), 440); clip(path.join(CLIPS, "camB", `camB_clip${n}.mp4`), 0); }
const listing = () => ["camA", "camB"].map(c => fs.readdirSync(path.join(CLIPS, c)).sort().map(f => { const st = fs.statSync(path.join(CLIPS, c, f)); return `${c}/${f}:${st.size}:${st.mtimeMs}`; }).join("|")).join("|");
const clipsBefore = listing();
const json = f => JSON.parse(fs.readFileSync(f, "utf8"));
const sha256 = f => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
const enc = p => p.split(WIN ? /[\\/]/ : "/").filter(Boolean).map(encodeURIComponent).join("/");   // as lib/format.ts mediaUrl

// ---------- servers and browser ----------
const freePort = () => new Promise(r => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
const servers = [];
async function startApp(sessions, env = {}) {
  const port = await freePort(), base = `http://127.0.0.1:${port}`;
  const proc = spawn(process.execPath, [NEXT, "start", "-H", "127.0.0.1", "-p", String(port)],
    { cwd: ROOT, env: { ...sandbox, CAMRIG_SESSIONS: sessions, ...env }, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  proc.log = "";
  proc.stdout.on("data", d => (proc.log += d)); proc.stderr.on("data", d => (proc.log += d));
  servers.push(proc);
  for (let i = 0; i < 150; i++) { try { if ((await fetch(base + "/")).ok) break; } catch {} await sleep(200); }
  return { port, base };
}
// A request outside the browser: its status, and its body as text.
const request = (port, method, p, body, headers = {}) => new Promise(r => {
  const q = http.request({ host: "127.0.0.1", port, path: p, method, headers: { "Content-Type": "application/json", ...headers } },
    res => { let t = ""; res.on("data", d => (t += d)); res.on("end", () => r([res.statusCode, t])); });
  q.on("error", () => r([0, ""]));
  q.end(body ? JSON.stringify(body) : undefined);
});
const status = async (...a) => (await request(...a))[0];
const cdpPort = await freePort();
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${path.join(scratch, "chrome")}`, "--no-first-run", "--mute-audio",
  "--hide-scrollbars", "--use-mock-keychain", "--password-store=basic", "--autoplay-policy=no-user-gesture-required", "--window-size=1440,900", "about:blank"],
  { stdio: "ignore", env: { ...process.env, TMPDIR: TMP } });
const extra = { cloud: null, sleeper: null, dev: null };

async function main() {
  let target;
  for (let i = 0; i < 60 && !target; i++) { await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json()).find(t => t.type === "page"); } catch {} }
  if (!target) throw new Error("Chrome did not start");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  let id = 0;
  const waiting = new Map(), errors = [];
  let here = "";                                  // the page go() opened last, named in each console error
  const send = (method, params = {}) => new Promise(r => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  ws.addEventListener("message", m => {
    const d = JSON.parse(m.data);
    if (d.id) { waiting.get(d.id)?.(d); waiting.delete(d.id); return; }
    if (d.sessionId) return;
    if (d.method === "Runtime.exceptionThrown") errors.push(`${here}: ${d.params.exceptionDetails.text} ${d.params.exceptionDetails.exception?.description ?? ""}`);
    if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push(`${here}: console.error ${d.params.args.map(a => a.value ?? a.description).join(" ")}`);
    if (d.method === "Log.entryAdded" && d.params.entry.level === "error") errors.push(`${here}: ${d.params.entry.text} ${d.params.entry.url ?? ""}`);
    if (d.method === "Page.javascriptDialogOpening") send("Page.handleJavaScriptDialog", { accept: true });
  });
  const js = async expression => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) throw new Error(`${expression.slice(0, 120)}: ${r.result.exceptionDetails.exception?.description}`);
    return r.result?.result?.value;
  };
  const run = (fn, ...args) => js(`(${fn})(${args.map(a => JSON.stringify(a)).join(",")})`);
  const until = async (fn, args = [], tries = 150, every = 200) => { for (let i = 0; i < tries; i++) { if (await run(fn, ...args).catch(() => false)) return true; await sleep(every); } return false; };
  const key = async (k, code, vk, modifiers = 0) => {
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers });
  };
  const type = text => send("Input.insertText", { text });
  // Typing goes through Chrome's input pipeline and run() does not, so a click sent next could overtake it: fill a
  // field, then wait until the page holds the text.
  const fillIn = async (sel, text) => {
    await run(q => { const i = document.querySelector(q); i.focus(); i.select(); }, sel);
    await type(text);
    return until((q, v) => document.querySelector(q)?.value === v, [sel, text]);
  };
  // A headless tab that draws no frames reports itself hidden, and React then never reveals streamed content.
  const go = async (base, url, ready) => {
    await send("Page.bringToFront");
    here = url;
    await send("Page.navigate", { url: base + url });
    await until(() => document.readyState === "complete");
    return until(ready, [], 200);
  };
  const shot = async name => {
    if (!SHOTS) return;
    await sleep(600);
    const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    fs.writeFileSync(path.join(SHOTS, `${name}.png`), Buffer.from(r.result.data, "base64"));
  };
  const shown = sel => run(s => !!document.querySelector(s), sel);
  const text = sel => run(s => document.querySelector(s)?.textContent ?? "", sel);
  const count = sel => run(s => document.querySelectorAll(s).length, sel);
  const noErrors = () => run(() => !document.querySelector("nextjs-portal, [data-nextjs-dialog], [data-screen=not-found]") && !/Application error|Unhandled Runtime Error/.test(document.body.innerText));
  // The page's words: the app's name only, never the internal one.
  const plain = async what => {
    const bad = await run(() => (document.title + "\n" + document.body.innerText).match(/.{0,40}\b(camrig|session viewer|viewer)\b.{0,40}/i)?.[0] ?? "");
    check(!bad, `${what} uses only the app's name${bad ? `: "${bad}"` : ""}`);
  };
  const t = { go, run, until, key, type, fillIn, shot, shown, text, count, noErrors, plain };

  await send("Runtime.enable"); await send("Page.enable"); await send("Log.enable");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  await localLane(t);
  await cloudLane(t);
  await signInLane(t);

  // A notFound() under a streamed Suspense boundary is finished by the client, and React reports that (#419) as it does.
  const unexpected = errors.filter(e => !/status of 409 .*\/api\/manual/.test(e) && !/^\/jobs\/nope: Uncaught Error: Minified React error #419/.test(e));
  check(unexpected.length === 0, `no console errors${unexpected.length ? `: ${unexpected.slice(0, 6).join("; ")}` : ""}`);
}

// ---------- A: library, New project, Run, single view, Multi-view, guards (not signed in) ----------
async function localLane({ go, run, until, key, type, fillIn, shot, shown, text, count, noErrors, plain }) {
  console.log("Library and New project");
  const SESS = path.join(scratch, "sessions");
  fs.mkdirSync(SESS);
  // an address with no key: not signed in, and the sign-in form starts with this address
  const A = await startApp(SESS, { CAMRIG_CLOUD_URL: "https://perception.example.com" }), at = (url, ready) => go(A.base, url, ready);
  check(await at("/", () => !!document.querySelector("[data-screen=library] [data-empty-library]")), "with no session yet, the library says to make one with New project");
  check(await run(() => document.visibilityState === "visible"), "the test tab is visible (else the streamed pages would stay hidden)");
  await plain("the library");

  await at("/new", () => !!document.querySelector(".np[data-ready] [data-np-path]"));
  await run(() => { const i = document.querySelector("[data-np-path]"); i.focus(); i.select(); });
  await type(path.join(scratch, "nothing here"));
  check(await until(() => document.querySelector("[data-np-check]")?.dataset.npCheck === "bad"), "a path being typed is checked by the server (a folder that is not there is refused)");
  await run(() => { const i = document.querySelector("[data-np-path]"); i.focus(); i.select(); });
  await type(scratch);
  check(await until(() => [...document.querySelectorAll("[data-np-dirs] button")].some(b => b.textContent === "e2e clips")), "the folder browser lists the scratch folder's subfolders");
  await run(() => [...document.querySelectorAll("[data-np-dirs] button")].find(b => b.textContent === "e2e clips").click());
  check(await until(() => document.querySelector("[data-np-check]")?.dataset.npCheck === "ok" && document.querySelectorAll("[data-np-cam]").length === 2
    && [...document.querySelectorAll("[data-np-cam] img")].every(i => i.complete && i.naturalWidth > 0)), "its two camera subfolders show with a picture, their files and length");
  check(/2 files · 0:06/.test(await text("[data-np-cam=camA]")), `each card gives the file count and length: "${(await text("[data-np-cam=camA] .muted")).trim()}"`);
  await plain("New project");
  check(await noErrors(), "New project has no error");
  await fillIn("[data-np-cam=camB] [data-np-name]", "cam2");
  await fillIn("[data-board=square_mm]", "25");
  await shot("new-project");
  await run(() => document.querySelector("[data-np-create]").click());
  check(await until(() => location.pathname === "/jobs/e2e_clips" && !!document.querySelector("[data-screen=overview] [data-run][data-ready]")), "Create the project writes it and opens its overview");
  const toml = path.join(SESS, "e2e_clips", "project.toml"), tomlText = fs.existsSync(toml) ? fs.readFileSync(toml, "utf8") : "";
  check(/name = "camA"/.test(tomlText) && /name = "cam2"/.test(tomlText) && /square_mm = 25/.test(tomlText) && tomlText.includes(JSON.stringify(path.join(CLIPS, "camB"))),
    "project.toml holds the confirmed names, the board and the camera folders");

  console.log("The overview and Run");
  const rows = () => run(() => [...document.querySelectorAll("[data-steps] tr[data-step]")].map(r => [r.dataset.step, r.dataset.stepStatus]));
  check(JSON.stringify((await rows()).map(r => r[0])) === '["cameras","scan","clock"]' && (await rows()).every(r => r[1] === ""), "the steps are cameras, scan and clock, none run yet");
  check(await shown("[data-run-start]") && !(await shown("[data-run] select")), "one Run button, with no choice of step");
  check(JSON.stringify(await run(() => [...document.querySelectorAll(".stepnav a")].map(a => new URL(a.href).pathname))) === '["/jobs/e2e_clips","/jobs/e2e_clips/multiview"]',
    "the session's pages are the overview and Multi-view only");
  check(await shown("[data-no-cameras]"), "before a run the overview says the cameras step lists the recordings");
  check(await shown("[data-upload][data-ready][data-upload-state=waiting_for_cloud] [data-cloud-signin]") && !(await shown("[data-upload-start]"))
    && await run(() => document.querySelector("[data-cloud-field=url]").value === "https://perception.example.com"),
    "not signed in, the upload panel shows the sign-in form with CAMRIG_CLOUD_URL as the server address, and no Upload button");
  await plain("the overview");
  check(await at("/jobs/e2e_clips/multiview", () => !!document.querySelector("[data-not-yet=clock]")) && /has not run yet/.test(await text("[data-not-yet=clock]")) && await noErrors(),
    "before a run Multi-view says the clock step has not run yet");
  await at("/jobs/e2e_clips", () => !!document.querySelector("[data-screen=overview] [data-run][data-ready] [data-run-start]"));
  await run(() => document.querySelector("[data-run-start]").click());
  check(await until(() => [...document.querySelectorAll("[data-step]")].some(r => r.dataset.stepNow === "running") || document.querySelector("[data-run-msg]")?.dataset.runMsg === "done"),
    "Run shows the steps running");
  check(await until(() => document.querySelector("[data-run-msg]")?.dataset.runMsg === "done" && [...document.querySelectorAll("[data-step]")].every(r => r.dataset.stepStatus), [], 900),
    `the run of every step finishes: "${await text("[data-run-msg]")}"`);
  const work = s => path.join(SESS, "e2e_clips", "work", s);
  const onDisk = ["cameras", "scan", "clock"].map(s => [s, fs.existsSync(path.join(work(s), "check.json")) ? json(path.join(work(s), "check.json")).status : "none"]);
  check(JSON.stringify(await rows()) === JSON.stringify(onDisk), `each step's status is its check.json's (${onDisk.map(r => r.join(" ")).join(", ")})`);
  const cams = json(path.join(work("cameras"), "cameras.json")), clockParts = Object.values(json(path.join(work("clock"), "clock.json")).cameras).reduce((n, c) => n + c.parts.length, 0);
  check(await until(() => document.querySelectorAll("section.cam[data-cam]").length === 2) && await count("section.cam tbody tr[data-file]") === cams.cameras.reduce((n, c) => n + c.parts.length, 0),
    `the overview then lists the recordings of cameras.json (${cams.cameras.map(c => `${c.name}: ${c.parts.length}`).join(", ")})`);
  check(await count(".g-bar") === clockParts, `the clock overview has a bar per part on the clock (${clockParts})`);
  check(await noErrors(), "the overview has no error");
  await plain("the overview after the run");
  await shot("overview-run");

  console.log("The library, the guards and the pages that are gone");
  clip(path.join(SESS, "one", "a", "part01.mp4"), 440);
  fs.writeFileSync(path.join(SESS, "one", "project.toml"), 'name = "one camera"\n[board]\nsquares = [5, 7]\nsquare_mm = 20.0\nmarker_mm = 14.0\n[[camera]]\nname = "a"\nkind = "action"\nfolder = "a"\n');
  check(await at("/", () => !!document.querySelector("[data-job=e2e_clips] .pills") && !!document.querySelector("[data-job=one]")), "the library lists both sessions");
  const pills = await run(() => [...document.querySelectorAll("[data-job=e2e_clips] .pills li")].map(li => [li.querySelector(".pill-name").textContent, li.querySelector("[data-status]").dataset.status]));
  check(JSON.stringify(pills) === JSON.stringify(onDisk), `its card shows each step's status (${pills.map(p => p.join(" ")).join(", ")})`);
  check(!(await shown("[data-job=e2e_clips] [data-upload-badge]")) && await shown("[data-job=one] [data-project-error]"), "a session never uploaded has no upload state, and a project file that cannot be read says why");
  check(await noErrors(), "the library has no error");
  await shot("library");
  const raw = (p, headers = {}) => new Promise(r => http.get({ host: "127.0.0.1", port: A.port, path: p, headers }, res => { res.resume(); r(res.statusCode); }).on("error", () => r(0)));
  check(await raw("/", { Host: `rebind.example:${A.port}` }) === 403 && await raw("/") === 200, "pages answer only to this machine's own address (a rebound name gets 403)");
  const video = path.join(CLIPS, "camA", "camA_clip1.mp4");
  check(await raw(`/api/media/e2e_clips/${enc(toml)}`) === 200 && await raw(`/api/media/e2e_clips/${enc(video)}`, { Range: "bytes=0-99" }) === 206
    && await raw("/api/media/e2e_clips/etc/passwd") === 403 && await raw(`/api/media/e2e_clips/${enc(path.join(SESS, "one", "project.toml"))}`) === 403
    && await raw(`/api/media/e2e_clips/${enc(path.join(CLIPS, "camA", "..", ".."))}`) !== 200,
    "files come only from the session folder and its video folders (a session's file and a video play; /etc/passwd and another session's files get 403)");
  check(await raw(`/api/media/e2e_clips/${enc(toml)}`, { "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "script" }) === 403, "a page on another site cannot read the session's files");
  const evil = { Origin: "http://evil.example" };
  check(await status(A.port, "POST", "/api/manual", { session: "e2e_clips", moves: { "cam2/part01": 1 } }, evil) === 403 && await status(A.port, "POST", "/api/run", { session: "e2e_clips" }, evil) === 403
    && await status(A.port, "POST", "/api/new", {}, { Origin: "null" }) === 403 && await status(A.port, "PUT", "/api/transcript", { video: "e2e_clips/camA/part01", cues: [], baseMtimeMs: null }, evil) === 403,
    "a page on another site cannot save a hand move, start a run, make a project or save a transcript (403)");
  const gone = await Promise.all(["/jobs/e2e_clips/positions", "/jobs/e2e_clips/hands", "/jobs/e2e_clips/analysis"].map(p => raw(p)));
  const transcribe = await status(A.port, "POST", "/api/transcribe", { video: "e2e_clips/camA/part01", fromMs: 0, toMs: 2000 });
  check(gone.every(s => s === 404) && [404, 405].includes(transcribe), `the positions, hands and analysis pages and the AI transcription are gone (${gone.join(", ")}; ${transcribe})`);
  check(await at("/jobs/nope", () => !!document.querySelector("[data-screen=not-found]")), "an unknown session gives the not-found page");
  await plain("the not-found page");

  console.log("The single view and its transcript");
  const SRT = path.join(SESS, "e2e_clips", "transcripts", "camA", "camA_clip1.srt");
  fs.mkdirSync(path.dirname(SRT), { recursive: true });
  fs.writeFileSync(SRT, "1\n00:00:00,200 --> 00:00:00,900\nThe first cue\n\n2\n00:00:01,000 --> 00:00:01,800\nThe second cue\n\n3\n00:00:02,000 --> 00:00:02,800\nThe third cue\n\n");
  check(await at("/jobs/e2e_clips/watch/camA/part01?t=1", () => !!document.querySelector("[data-screen=single][data-ready]") && document.querySelector("[data-main-video]")?.readyState >= 1),
    "the single view of camA part01 opens and its video loads");
  check(await until(() => document.querySelectorAll(".cue-list [role=listitem]").length === 3), "the editor shows the three cues of the person's transcript");
  check(!(await shown("[data-transcribe], [data-ai-all], .selbar")) && !/AI range|with AI/.test(await run(() => document.body.innerText)), "there is no AI transcription");
  check(await noErrors(), "the single view has no error");
  await plain("the single view");
  await run(() => document.querySelector('.cue[data-n="2"] .cue-x').click());
  check(await until(() => document.activeElement?.classList.contains("ec-text")), "clicking a cue's text opens it for editing");
  await run(() => document.activeElement.select());
  await type("The second cue, edited (checked)");
  check(await until(() => document.querySelector(".status")?.dataset.state === "dirty"), "the edit shows as not saved yet");
  await key("s", "KeyS", 83, WIN ? 2 : 4);
  check(await until(() => document.querySelector(".status")?.dataset.state === "saved"), `${WIN ? "Ctrl" : "Cmd"}+S saves`);
  const saved = parseSrt(fs.readFileSync(SRT, "utf8")).cues, history = path.join(path.dirname(SRT), ".transcript_history");
  check(saved.length === 3 && saved[1].text === "The second cue, edited (checked)" && fs.existsSync(SRT.replace(/\.srt$/, ".txt")) && fs.readdirSync(history).length >= 2,
    "it lands in the session's transcripts/camA/ with its .txt, and the version it replaced is kept in .transcript_history");
  await shot("single");
  check(await at("/jobs/e2e_clips/watch/cam2/part01", () => !!document.querySelector("[data-screen=single][data-ready] [data-empty]")) && /records no sound/.test(await text("[data-empty]")),
    "a silent video opens with an empty transcript that says the camera records no sound");

  console.log("Multi-view");
  check(await at("/jobs/e2e_clips/multiview", () => document.querySelectorAll(".panel[data-cam]").length === 2 && !!window.__oneClock), "Multi-view opens with a panel per camera");
  check(await count("[data-need]") > 0 && await shown("[data-hand-move]"), `the parts the clock step could not place are listed under To place by hand (${(await run(() => [...document.querySelectorAll("[data-need]")].map(b => b.dataset.need))).join(", ")})`);
  check(await noErrors(), "Multi-view has no error");
  await plain("Multi-view");
  await shot("multiview");
}

// ---------- B: upload, sync by hand and stopping uploaders, signed in for a demo ----------
// hand: camera a with a sound clip and a silent one, camera b with one silent clip, through cameras, scan and clock.
// boot: one clip, whose progress.json says running with a process id that, as after a restart of the computer, now
// belongs to an unrelated process (a sleep started here).
const CLOUD = path.join(scratch, "cloud");
// The uploaders of the sessions under CLOUD, running or waiting, stopped as cloud/demo.py stops them on Ctrl+C.
const stopUploaders = () => {
  const r = spawnSync(PY, [...PY_UTF8, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import demo; print(*demo.uploaders(sys.argv[2:]))", path.join(REPO, "cloud"), CLOUD], { encoding: "utf8", env: sandbox });
  const pids = r.stdout.trim().split(/\s+/).filter(Boolean).map(Number).filter(p => p > 0 && p !== process.pid);
  for (const pid of pids) try { process.kill(pid, "SIGTERM"); } catch {}
  return pids;
};
const board = '[board]\nsquares = [5, 7]\nsquare_mm = 20.0\nmarker_mm = 14.0\n', cam = n => `[[camera]]\nname = "${n}"\nkind = "action"\nfolder = "${n}"\n`;

async function cloudLane({ go, run, until, key, fillIn, shot, shown, text, count, noErrors, plain }) {
  console.log("Upload to the cloud, and sync by hand");
  const HAND = path.join(CLOUD, "hand"), BOOT = path.join(CLOUD, "boot");
  clip(path.join(HAND, "a", "part01.mp4"), 440); clip(path.join(HAND, "a", "part02.mp4"), 0); clip(path.join(HAND, "b", "part01.mp4"), 0);
  fs.writeFileSync(path.join(HAND, "project.toml"), `name = "hand sync"\n${board}${cam("a")}${cam("b")}`);
  const sync = spawnSync(PY, ["-m", "camrig.run", path.join(HAND, "project.toml"), "--only", "cameras,scan,clock"], { cwd: REPO, encoding: "utf8", env: sandbox });
  check(fs.existsSync(path.join(HAND, "work", "clock", "clock.json")), `the hand session's cameras, scan and clock ran: ${sync.stdout.split("\n").filter(l => /^\w+\s+[A-Z]/.test(l)).join("; ")}`);
  clip(path.join(BOOT, "a", "part01.mp4"), 440); clip(path.join(BOOT, "b", "part01.mp4"), 440);
  fs.writeFileSync(path.join(BOOT, "project.toml"), `name = "cut off"\n${board}${cam("a")}${cam("b")}`);
  fs.mkdirSync(path.join(BOOT, "upload"));
  extra.sleeper = spawn(process.execPath, ["-e", "setTimeout(() => {}, 600_000)"], { stdio: "ignore" });   // Windows has no sleep
  fs.writeFileSync(path.join(BOOT, "upload", "progress.json"), JSON.stringify({ schema: "camrig.upload.progress/1", pid: extra.sleeper.pid, state: "running", started: "2026-11-08T00:00:00Z",
    updated: "2026-11-08T00:00:01Z", bytes_total: 1, bytes_done: 0, rate_bps: 0, eta_s: null, error: "", files: [] }));

  extra.cloud = spawn(PY, [path.join(ROOT, "tests", "slow_cloud.py"), "0.8", "--port", "0", "--dir", path.join(scratch, "cloud-data")], { stdio: ["ignore", "pipe", "ignore"], env: sandbox });
  const ready = await new Promise(r => extra.cloud.stdout.once("data", d => r(String(d).trim().split(" "))));
  const [cloudUrl, cloudKey] = [ready[1], ready.at(-1)];
  const B = await startApp(CLOUD, { CAMRIG_CLOUD_URL: cloudUrl, CAMRIG_CLOUD_KEY: cloudKey }), at = (url, ready_) => go(B.base, url, ready_);
  const inCloud = async s => (await (await fetch(`${cloudUrl}/sessions/${s}/files`, { headers: { Authorization: `Bearer ${cloudKey}` } })).json()).files;
  const progress = s => { try { return json(path.join(CLOUD, s, "upload", "progress.json")); } catch { return null; } };

  // the cut-off upload goes on by itself when the app starts
  let bootDone = false;
  for (let i = 0; i < 60 && !bootDone; i++) { bootDone = progress("boot")?.state === "done"; if (!bootDone) await sleep(500); }
  const bootFiles = bootDone ? await inCloud("boot") : [];
  check(bootDone && bootFiles.some(f => f.path === "originals/a/part01.mp4" && f.sha256 === sha256(path.join(BOOT, "a", "part01.mp4"))) && bootFiles.some(f => f.path === "manifest.json")
    && extra.sleeper.exitCode == null && extra.sleeper.signalCode == null,
    `an upload cut off by a restart goes on when the app starts, though its old process id now belongs to another process (left alone): boot ends ${progress("boot")?.state}, ${bootFiles.length} files in the cloud`);
  check(await at("/", () => document.querySelector("[data-job=boot] [data-upload-badge]")?.dataset.uploadBadge === "In the cloud" && !!document.querySelector("[data-job=hand]")),
    "the library card says the cut-off session is in the cloud");
  check(await run(() => !document.querySelector("[data-job=hand] [data-upload-badge]")), "a session never uploaded has no upload state on its card");
  const [evil] = await request(B.port, "POST", "/api/upload", { session: "hand" }, { Origin: "http://evil.example" });
  const [, got] = await request(B.port, "GET", "/api/upload?session=boot");
  check(evil === 403 && !got.includes(cloudKey) && got.includes("\"cloud\":true"), "a page on another site cannot start an upload (403), and the lab key never leaves the server");

  // start, running, pause, paused, resume, done
  check(await at("/jobs/hand", () => !!document.querySelector("[data-upload][data-ready][data-upload-state=none] [data-upload-start]")), "the hand session's overview offers Upload");
  check(/Signed in for this demo/.test(await text("[data-cloud-signed-in]")) && !(await shown("[data-cloud-signout]")), "signed in for a demo, with no Sign out");
  await run(() => document.querySelector("[data-upload-start]").click());
  check(await until(() => !!document.querySelector("[data-upload-state=running]") && document.querySelectorAll("[data-upload-status=done]").length >= 1
    && document.querySelectorAll("[data-upload-status=done]").length < document.querySelectorAll("[data-upload-file]").length, [], 150),
    `the upload runs: ${await text("[data-upload-total]")}`);
  check(await count("[data-upload-group=original] [data-upload-file]") === 3 && await count("[data-upload-group=sync] [data-upload-file]") >= 8 && await shown("[data-upload-total] [role=progressbar]"),
    "its files are listed as originals and sync, under one bar for the whole session");
  await shot("upload-running");
  const plainUpload = () => run(() => !/\b(MB|GB|kB|bytes|pieces|Fingerprint)/.test(document.querySelector("[data-upload]").textContent));
  check(await plainUpload() && /^\d+%/.test(await text("[data-upload-total]")), `while it runs the panel shows a percent and no sizes, pieces or fingerprints: ${await text("[data-upload-total]")}`);
  await plain("the overview while it uploads");
  // a second Start while it runs: one more uploader waits for it, the panel goes on saying running, and the Pause below
  // stops the waiting one too
  await request(B.port, "POST", "/api/upload", { session: "hand" });
  let again = "";
  for (let i = 0; i < 16 && again !== "running"; i++) { await sleep(250); again = JSON.parse((await request(B.port, "GET", "/api/upload?session=hand"))[1] || "{}").state; }
  check(again === "running", `a second Start while the upload runs leaves the panel saying running, not starting (${again})`);
  await run(() => document.querySelector("[data-upload-pause]").click());
  check(await until(() => !!document.querySelector("[data-upload-state=paused]"), [], 100), "Pause stops it after the piece in flight");
  const paused = progress("hand"), donePaused = paused?.files.filter(f => f.status === "done").length ?? 0;
  check(paused?.state === "paused" && donePaused > 0 && donePaused < paused.files.length && !fs.existsSync(path.join(HAND, "upload", "pause.request")),
    `progress.json says paused with ${donePaused} of ${paused?.files.length} files done, and the uploader took its pause.request away`);
  await shot("upload-paused");
  check(await at("/", () => /^Upload paused at \d+%$/.test(document.querySelector("[data-job=hand] [data-upload-badge]")?.dataset.uploadBadge ?? "")),
    `the library card says ${await run(() => document.querySelector("[data-job=hand] [data-upload-badge]")?.dataset.uploadBadge)}`);
  await at("/jobs/hand", () => !!document.querySelector("[data-upload][data-ready][data-upload-state=paused] [data-upload-start]"));
  check(/Resume/.test(await text("[data-upload-start]")), "the button now says Resume");
  await run(() => document.querySelector("[data-upload-start]").click());
  check(await until(() => !!document.querySelector("[data-upload-state=done] [data-in-cloud]"), [], 200), "Resume goes on to done, and the page lists what the cloud holds");
  check(await text("[data-upload-msg]") === "Uploaded. Everything is in the cloud." && await plainUpload(), "done says 'Uploaded. Everything is in the cloud.', with no sizes");
  const handFiles = await inCloud("hand"), local = Object.fromEntries(progress("hand").files.map(f => [f.path, f.size]));
  const want = { "originals/a/part01.mp4": "a/part01.mp4", "originals/a/part02.mp4": "a/part02.mp4", "originals/b/part01.mp4": "b/part01.mp4",
    "sync/project.toml": "project.toml", "sync/clock.json": "work/clock/clock.json", "sync/decisions.json": "decisions.json" };
  check(Object.entries(want).every(([k, f]) => handFiles.find(x => x.path === k)?.sha256 === sha256(path.join(HAND, f))) && handFiles.every(f => local[f.path] === f.size)
    && await count("[data-cloud-file]") === handFiles.length, `the cloud holds all ${handFiles.length} files at their sizes, the originals and sync files byte for byte (sha256), and the page lists them`);
  await shot("upload-done");

  // a project the uploader refuses before it writes anything: the panel gives the uploader's own reason
  clip(path.join(CLOUD, "one", "a", "part01.mp4"), 440);
  fs.writeFileSync(path.join(CLOUD, "one", "project.toml"), `name = "one camera"\n${board}${cam("a")}`);
  await request(B.port, "POST", "/api/upload", { session: "one" });
  let refused = "";
  for (let i = 0; i < 60 && !/at least two \[\[camera\]\]/.test(refused); i++) {
    await sleep(250);
    const st = JSON.parse((await request(B.port, "GET", "/api/upload?session=one"))[1] || "{}");
    refused = st.exit ?? (st.state === "failed" ? st.progress?.error : "") ?? "";
  }
  check(/at least two \[\[camera\]\]/.test(refused), `a refused project shows the uploader's reason (its exit, or its failed progress.json): ${refused}`);

  // sync by hand: the silent clips are listed, placed by marking a moment and by typing a start, saved, and the clock rerun
  check(await at("/jobs/hand/multiview", () => !!window.__oneClock && document.querySelectorAll("[data-need]").length > 0), "Multi-view of the hand session opens");
  const needs = await run(() => [...document.querySelectorAll("[data-need]")].map(b => b.dataset.need));
  check(JSON.stringify(needs) === JSON.stringify(["a/part02", "b/part01"]) && /left out/.test(await text('[data-need="b/part01"]')) && /no sound/.test(await text('[data-need="a/part02"]'))
    && /To place by hand/.test(await text("[data-hand-move]")), `the parts to place by hand are listed under To place by hand with their first guess: ${needs.join(", ")}`);
  check(await count('.panel[data-cam="b"]') === 1, "the camera left out of clock.json gets a panel, at its first guess");
  await shot("sync-list");
  await run(() => document.querySelector('[data-need="b/part01"]').click());
  await run(() => window.__oneClock.showClock(1.0));
  await run(() => document.querySelector("[data-mark-placed]").click());
  await run(() => window.__oneClock.showClock(2.0));
  await run(() => document.querySelector("[data-mark-here]").click());
  check(await until(() => Math.abs(window.__oneClock.s.cameras.find(c => c.key === "b").parts[0].start_s + 1) < 1e-6),
    "marking clock 1.0 in camera a and frame 60 of b/part01 as the same moment starts b/part01 at clock −1.0");
  await run(() => document.querySelector("[data-slide-save]").click());
  check(await until(() => /^Saved/.test(document.querySelector("[data-slide-msg]")?.textContent ?? "")), "Save says it is saved");
  await run(() => document.querySelector('[data-need="a/part02"]').click());
  await fillIn("[data-slide-start]", "0:05");
  await key("Enter", "Enter", 13);
  await until(() => document.querySelector("[data-slide-start]").value === "0:05.000");
  await run(() => document.querySelector("[data-slide-1s-fwd]").click());
  check(await until(() => Math.abs(window.__oneClock.s.cameras.find(c => c.key === "a").parts.find(p => p.key === "part02").start_s - 6) < 1e-6),
    "typing 0:05 as a/part02's start, then +1 s, puts it at clock 6.0");
  await fillIn("[data-slide-note]", "e2e: typed");
  await run(() => document.querySelector("[data-slide-save]").click());
  await until(() => /^Saved/.test(document.querySelector("[data-slide-msg]")?.textContent ?? ""));
  const manual = json(path.join(HAND, "decisions.json")).clock?.user?.manual ?? {};
  check(Math.abs(manual["b/part01"]?.start_s + 1) < 1e-6 && manual["b/part01"]?.file === "part01.mp4" && manual["a/part02"]?.start_s === 6 && manual["a/part02"]?.note === "e2e: typed",
    `decisions.json clock.user.manual holds both: ${JSON.stringify(manual)}`);
  await run(() => document.querySelector("[data-rerun-clock]").click());
  check(await until(() => document.querySelector("[data-rerun-state]")?.dataset.rerunState === "done", [], 300), "Rerun the clock runs the clock step");
  check(/the cameras now follow the new clock/.test(await text("[data-rerun-state]")) && !(await shown("[data-run-later]")), "it says the cameras follow the new clock, and offers nothing more to run");
  const placed = json(path.join(HAND, "work", "clock", "clock.json")).cameras;
  check(placed.b?.parts[0].source === "manual" && Math.abs(placed.b.parts[0].start_s + 1) < 1e-6 && placed.a.parts[1].source === "manual" && placed.a.parts[1].start_s === 6,
    `clock.json now places b/part01 at ${placed.b?.parts[0].start_s} and a/part02 at ${placed.a.parts[1].start_s}, both by hand`);
  check(await until(() => !document.querySelector("[data-need]") && !!document.querySelector('.panel[data-cam="b"]')), "nothing is left to place by hand, and camera b is on the clock");
  await run(() => window.__oneClock.showClock(0.5));
  await until(() => window.__oneClock.panels.filter(p => p.part).every(p => p.video.readyState >= 2));
  check(await noErrors(), "Multi-view has no error");
  await plain("Multi-view after the hand move");
  await shot("sync-placed");
  let synced = false;
  for (let i = 0; i < 90 && !synced; i++) {
    const f = await inCloud("hand"), sha = p => f.find(x => x.path === p)?.sha256;
    synced = sha("sync/decisions.json") === sha256(path.join(HAND, "decisions.json")) && sha("sync/clock.json") === sha256(path.join(HAND, "work", "clock", "clock.json")) && progress("hand")?.state === "done";
    if (!synced) await sleep(500);
  }
  check(synced, "the uploader started again after the Save and the clock rerun: the cloud's decisions.json and clock.json are the new ones");

  // stopping: the uploader that runs and the one waiting for it both stop; a half-written upload/lock is not read
  const STOP = path.join(CLOUD, "stop");
  const big = path.join(STOP, "a", "part01.mp4"); // 15 MiB, sparse: three pieces of 5 MiB keep the runner busy for seconds
  fs.mkdirSync(path.dirname(big), { recursive: true });
  fs.writeFileSync(big, "");
  fs.truncateSync(big, 15 << 20);
  clip(path.join(STOP, "b", "part01.mp4"), 440);
  fs.writeFileSync(path.join(STOP, "project.toml"), `name = "stop"\n${board}${cam("a")}${cam("b")}`);
  const env = { ...sandbox, CAMRIG_CLOUD_URL: cloudUrl, CAMRIG_CLOUD_KEY: cloudKey };
  const uploader = () => { const c = spawn(PY, ["-m", "camrig.upload", path.join(STOP, "project.toml"), "--part-mb", "5", "--parallel", "1"], { cwd: REPO, env, stdio: "ignore" });
    c.ended = new Promise(r => c.once("exit", r)); return c; };
  const runner = uploader();
  for (let i = 0; i < 40 && progress("stop")?.pid !== runner.pid; i++) await sleep(250);
  const waiter = uploader();
  await sleep(1500);
  fs.writeFileSync(path.join(BOOT, "upload", "lock"), "");
  const stopped = stopUploaders();
  const ended = await Promise.race([Promise.all([runner.ended, waiter.ended]).then(() => true), sleep(5000).then(() => false)]);
  check(stopped.includes(runner.pid) && stopped.includes(waiter.pid) && ended && progress("stop")?.state !== "done",
    `stopping the uploaders finds the running one and the one waiting for it by their command line and both end (stopped ${stopped.join(", ")}; runner ${runner.pid}, waiter ${waiter.pid})`);
  if (!ended) { runner.kill(); waiter.kill(); }
}

// ---------- C: sign in from the panel, with the local stand-in for the cloud and the test password safe ----------
async function signInLane({ go, run, until, fillIn, shot, shown, text, plain }) {
  console.log("Sign in to the cloud");
  const SIGN = path.join(CLOUD, "sign");
  clip(path.join(SIGN, "a", "part01.mp4"), 440); clip(path.join(SIGN, "b", "part01.mp4"), 440);
  fs.writeFileSync(path.join(SIGN, "project.toml"), `name = "sign in"\n${board}${cam("a")}${cam("b")}`);
  extra.dev = spawn(PY, [path.join(REPO, "cloud", "dev_server.py"), "--port", "0", "--dir", path.join(scratch, "devcloud")], { stdio: ["ignore", "pipe", "ignore"], env: sandbox });
  const devUrl = (await new Promise(r => extra.dev.stdout.once("data", d => r(String(d).trim().split(" ")))))[1];
  const C = await startApp(SIGN), at = (url, ready) => go(C.base, url, ready);
  const send3 = async (method, body, headers = {}) => { const [s, t] = await request(C.port, method, "/api/cloud", body, headers); let j = null; try { j = JSON.parse(t); } catch {} return [s, j]; };
  check(await at("/jobs/sign", () => !!document.querySelector("[data-upload][data-ready] [data-cloud-signin]") && !document.querySelector("[data-upload-start]")
    && document.querySelector("[data-cloud-field=url]").value === "" && document.querySelector("[data-cloud-field=url]").placeholder === "https://…"),
    "not signed in and with no address set, the panel shows the sign-in form with an empty server address");
  check(JSON.stringify(await run(() => [...document.querySelectorAll("[data-cloud-signin] label")].map(l => l.firstChild.textContent))) === '["Server address","Email","Password"]'
    && /^Sign in$/.test(await text("[data-cloud-signin-go]")), "the form asks for the server address, the email and the password");
  await plain("the sign-in form");
  await shot("cloud-signed-out");
  const [evil3] = await send3("POST", { url: devUrl, email: "lab@example.com", password: "lab-dev-password" }, { Origin: "http://evil.example" });
  const [badCode, bad] = await send3("POST", { url: devUrl, email: "lab@example.com", password: "wrong" });
  check(evil3 === 403 && badCode === 400 && /wrong email or password/.test(bad?.error ?? ""), `a page on another site cannot sign in (403), and a wrong password gets the cloud's reason: ${bad?.error}`);
  for (const [k, v] of [["url", devUrl], ["email", "lab@example.com"], ["password", "lab-dev-password"]]) await fillIn(`[data-cloud-field=${k}]`, v);
  await run(() => document.querySelector("[data-cloud-signin-go]").click());
  check(await until(() => document.querySelector("[data-cloud-signed-in]")?.textContent.includes("Signed in as lab@example.com (lab dev)") && !!document.querySelector("[data-upload-start]"), [], 100),
    `Sign in shows "${await text("[data-cloud-signed-in] span")}" and offers Upload`);
  const cfg = fs.existsSync(path.join(HOME, ".camrig", "cloud.json")) ? json(path.join(HOME, ".camrig", "cloud.json")) : {};
  const safe = fs.existsSync(path.join(SAFE, "keyring.json")) ? fs.readFileSync(path.join(SAFE, "keyring.json"), "utf8") : "";
  const [, got3] = await send3("GET");
  check(cfg.url === devUrl && cfg.email === "lab@example.com" && !("key" in cfg) && !("token" in cfg) && /set/.test(fs.readFileSync(path.join(SAFE, "asked.txt"), "utf8"))
    && safe.includes(devUrl) && !JSON.stringify(got3).includes(Object.values(JSON.parse(safe || "{}"))[0] ?? "none") && !safe.includes("lab-dev-password"),
    "the token went to the test password safe, cloud.json in the temporary home holds no token, and the app's answer never carries it");
  await run(() => window.scrollTo(0, 0));
  await shot("cloud-signed-in");
  await run(() => document.querySelector("[data-upload-start]").click());
  check(await until(() => !!document.querySelector("[data-upload-state=done] [data-in-cloud]"), [], 200), "Upload sends the session, and the panel lists what the cloud holds");
  const devFiles = await (await fetch(`${devUrl}/sessions/sign/files`, { headers: { Authorization: `Bearer ${Object.values(JSON.parse(safe || "{}"))[0]}` } })).json().catch(() => ({}));
  check(devFiles.files?.some(f => f.path === "originals/a/part01.mp4" && f.sha256 === sha256(path.join(SIGN, "a", "part01.mp4"))), `the stand-in cloud holds the session (${devFiles.files?.length ?? 0} files)`);
  await run(() => document.querySelector("[data-cloud-signout]").click());
  check(await until(() => !!document.querySelector("[data-cloud-signin]") && !document.querySelector("[data-upload-start]"), [], 100)
    && !fs.readFileSync(path.join(SAFE, "keyring.json"), "utf8").includes(devUrl), "Sign out brings the form back and the test password safe forgets the token");
  check(await at("/jobs/sign", () => !!document.querySelector("[data-upload][data-ready] [data-cloud-signin]")) && await run(() => document.querySelector("[data-cloud-field=url]").value === "")
    && json(path.join(HOME, ".camrig", "cloud.json")).url === devUrl, "opened again, the form's server address is empty: only CAMRIG_CLOUD_URL fills it in, not the address kept from before");
}

try {
  await main();
} catch (e) {
  console.log(`  FAIL  ${e.stack}`);
  failed++;
} finally {
  chrome.kill();
  for (const s of servers) s.kill();
  extra.cloud?.kill();
  extra.sleeper?.kill();
  extra.dev?.kill();
  stopUploaders();
  await sleep(500);
  check(listing() === clipsBefore, "the recordings New project used are unchanged (listed by name, size and time)");
  check(realCamrig() === realCamrigBefore, `the real ~/.camrig is unchanged (${realCamrigBefore ?? "absent"})`);
  if (failed) for (const s of servers) { const lines = s.log.split("\n").filter(l => /error/i.test(l)).slice(0, 10); if (lines.length) console.log(lines.join("\n")); }
  fs.rmSync(scratch, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
console.log(failed ? `FAIL (${failed})` : "PASS");
process.exit(failed ? 1 : 0);
