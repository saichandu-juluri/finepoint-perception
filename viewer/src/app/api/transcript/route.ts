// GET ?video=<session>/<camera>/<part>: the transcript of that video (the person's edited one, else the transcript
// step's), its cues and the edited file's modified time.
// PUT {video, cues, baseMtimeMs, force?}: writes the session's transcripts/<camera>/<stem>.srt and .txt; 409 when the
// edited file changed on disk meanwhile. PUT {video, cues, stash: true}: only keeps the cues in .transcript_history/.
import { answer, HttpError, readTranscript, saveTranscript, stashTranscript } from "@/server/data";
import type { Cue } from "@/lib/srt";

export const GET = (req: Request) => answer(req, async () => {
  const video = new URL(req.url).searchParams.get("video");
  if (!video) throw new HttpError(400, "Which video?");
  return readTranscript(video);
});

export const PUT = (req: Request) => answer(req, async () => {
  const body = (await req.json().catch(() => null)) as { video?: unknown; cues?: unknown; baseMtimeMs?: unknown; force?: unknown; stash?: unknown } | null;
  const valid = body && typeof body.video === "string" && Array.isArray(body.cues) && body.cues.length <= 50000 &&
    (body.stash === true || body.baseMtimeMs === null || typeof body.baseMtimeMs === "number") &&
    body.cues.every(c => c && typeof c === "object" && typeof c.startMs === "number" && typeof c.endMs === "number" && typeof c.text === "string");
  if (!valid) throw new HttpError(400, "Expected {video, cues: [{startMs, endMs, text}], baseMtimeMs}");
  const cues = (body.cues as Cue[]).map(({ startMs, endMs, text }) => ({ startMs, endMs, text }));
  if (body.stash === true) return stashTranscript(body.video as string, cues);
  return saveTranscript(body.video as string, cues, body.baseMtimeMs as number | null, body.force === true);
}, true);
