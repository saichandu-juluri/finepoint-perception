// GET ?session=: the session's upload (progress.json, whether its uploader runs) and, once it is not sending, what the
// cloud holds. POST {session}: starts or resumes the uploader. DELETE ?session=: pauses it after the pieces in flight.
// Poll GET about every 2 s while it sends.
import { apiSession } from "@/server/camrig";
import { answer } from "@/server/data";
import { pauseUpload, startUpload, uploadState } from "@/server/upload";

const sessionOf = (req: Request) => apiSession(new URL(req.url).searchParams.get("session"));
export const GET = (req: Request) => answer(req, async () => uploadState(await sessionOf(req)));
export const DELETE = (req: Request) => answer(req, async () => pauseUpload(await sessionOf(req)), true);
export const POST = (req: Request) => answer(req, async () => {
  const body = (await req.json().catch(() => null)) as { session?: unknown } | null;
  return startUpload(await apiSession(body?.session));
}, true);
