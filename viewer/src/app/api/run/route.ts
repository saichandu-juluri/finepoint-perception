// GET ?session=: each step's state and the last run started here. POST {session, only?}: starts
// `python -m camrig.run <project.toml> [--only <step>]` and answers at once (409 while any job runs). DELETE ?session=
// cancels it. Poll GET about every 2 s while it runs.
import { apiSession } from "@/server/camrig";
import { answer } from "@/server/data";
import { cancelRun, runState, startRun } from "@/server/run";

const sessionOf = (req: Request) => apiSession(new URL(req.url).searchParams.get("session"));
export const GET = (req: Request) => answer(req, async () => runState(await sessionOf(req)));
export const DELETE = (req: Request) => answer(req, async () => cancelRun(await sessionOf(req)), true);
export const POST = (req: Request) => answer(req, async () => {
  const body = (await req.json().catch(() => null)) as { session?: unknown; only?: unknown } | null;
  return startRun(await apiSession(body?.session), typeof body?.only === "string" && body.only ? body.only : null);
}, true);
