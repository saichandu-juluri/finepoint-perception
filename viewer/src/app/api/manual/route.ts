// POST {session, moves: {"cam/part": start_s}, note?}: the parts' starts set by hand, into decisions.json clock.user.manual.
// DELETE ?session=&part=: that hand setting removed. The clock step applies either on its next run. Either starts the
// session's uploader again, so the change goes up.
import { apiSession } from "@/server/camrig";
import { answer } from "@/server/data";
import { removeManual, saveManual } from "@/server/manual";
import { restartUpload } from "@/server/upload";

export const POST = (req: Request) => answer(req, async () => {
  const b = (await req.json().catch(() => null)) as { session?: unknown; moves?: unknown; note?: unknown } | null;
  const ref = await apiSession(b?.session), r = await saveManual(ref, b?.moves, b?.note);
  restartUpload(ref);
  return r;
}, true);
export const DELETE = (req: Request) => answer(req, async () => {
  const q = new URL(req.url).searchParams, ref = await apiSession(q.get("session")), r = await removeManual(ref, q.get("part"));
  restartUpload(ref);
  return r;
}, true);
