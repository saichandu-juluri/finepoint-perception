// A part moved by hand in Multi-view: decisions.json clock.user.manual["cam/part"] = {start_s, file, note}, which the clock
// step reads on its next run (docs/FORMATS.md). Only the person's "user" section is touched; the file is read, merged and
// written whole in one rename, so every other key stays. A running step saves into the same file, so both take
// .decisions.json.lock around their read and write (camrig/decisions.py), and a step keeps what was saved here meanwhile.
import fsp from "node:fs/promises";
import path from "node:path";
import { loadSession, project, type SessionRef } from "./camrig";
import { movable } from "@/lib/session";
import { HttpError, renameOver, statOrNull } from "./data";

const SCHEMA = "camrig.decisions/1";
const STALE_MS = 30_000; // as camrig/decisions.py: a lock this old was left by a writer that died

async function locked<T>(dir: string, fn: () => Promise<T>): Promise<T> {
  const lock = path.join(dir, ".decisions.json.lock");
  for (const t0 = Date.now(); ; ) {
    try {
      await (await fsp.open(lock, "wx")).close();
      break;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
      const st = await statOrNull(lock);
      if (st && Date.now() - st.mtimeMs > STALE_MS) { await fsp.rm(lock, { force: true }); continue; }
      if (Date.now() - t0 > 2 * STALE_MS) throw new HttpError(409, "decisions.json stays locked by another writer. Try again.");
      await new Promise(r => setTimeout(r, 50));
    }
  }
  try { return await fn(); } finally { await fsp.rm(lock, { force: true }); }
}

async function edit(ref: SessionRef, change: (manual: Record<string, unknown>) => void) {
  const p = await project(ref);
  if (p.ok && p.clock_existing) throw new HttpError(409, "This session keeps a clock made earlier ([clock] existing in project.toml), which takes no hand moves.");
  const file = path.join(ref.dir, "decisions.json");
  await locked(ref.dir, async () => {
    const text = await fsp.readFile(file, "utf8").catch(e => ((e as NodeJS.ErrnoException).code === "ENOENT" ? null : Promise.reject(e)));
    const data = text == null ? { schema: SCHEMA } : JSON.parse(text);
    if (data.schema !== SCHEMA) throw new HttpError(409, "decisions.json is in a form this app does not read, so it was left as it is.");
    const clock = (data.clock ??= { auto: {}, evidence: {}, user: {} });
    clock.user ??= {};
    change((clock.user.manual ??= {}));
    if (!Object.keys(clock.user.manual).length) delete clock.user.manual;
    const tmp = `${file}.${crypto.randomUUID()}.part`;
    await fsp.writeFile(tmp, JSON.stringify(data, null, 1));
    await renameOver(tmp, file);
  });
}

// moves: {"cam/part": start_s}, one part or every part of a camera, written in one go.
export async function saveManual(ref: SessionRef, moves: unknown, note: unknown) {
  const s = await loadSession(ref, true), list = moves && typeof moves === "object" ? Object.entries(moves) : [];
  if (!list.length || list.some(([, v]) => typeof v !== "number" || !Number.isFinite(v))) throw new HttpError(400, "Expected {moves: {\"cam/part\": start_s}}");
  const files: Record<string, string> = {};
  for (const [key] of list) {
    const [cam, part] = key.split("/");
    const c = s?.cameras.find(x => x.key === cam), found = c?.parts.find(p => p.key === part);
    if (!s || !c || !found) throw new HttpError(404, `${key} is no part of this session.`);
    if (!movable(s, c, found)) throw new HttpError(400, `${key} is on the reference camera, whose clock is the session's, so it cannot be moved.`);
    files[key] = path.basename(found.file);
  }
  const why = typeof note === "string" && note.trim() ? note.trim() : "moved by hand in Multi-view";
  // the file name too: a part key is only a place in time order, which changes when a part is added or taken away
  await edit(ref, m => { for (const [key, v] of list) m[key] = { start_s: Math.round((v as number) * 1e6) / 1e6, file: files[key], note: why }; });
  return { saved: Object.fromEntries(list) };
}

export async function removeManual(ref: SessionRef, key: unknown) {
  if (typeof key !== "string") throw new HttpError(400, "Which part?");
  await edit(ref, m => { delete m[key]; });
  return { removed: key };
}
