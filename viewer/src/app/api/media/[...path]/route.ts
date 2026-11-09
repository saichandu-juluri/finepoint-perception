// A file of a session by its absolute path, /api/media/<session>/<path>, with byte ranges so the 4 to 6 GB videos play and
// seek without being read whole. Only files inside the session folder or the video folders its project.toml names.
import fs from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { resolveMedia, sessionRef } from "@/server/camrig";
import { HttpError, readAllowed } from "@/server/data";

const TYPES: Record<string, string> = {
  ".mov": "video/mp4", // H.264 in QuickTime: Chrome plays it when told mp4, not when told quicktime
  ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".json": "application/json",
  ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".srt": "text/plain; charset=utf-8",
  ".vtt": "text/vtt; charset=utf-8", ".log": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
};

type Ctx = { params: Promise<{ path: string[] }> };

async function serve(req: Request, { params }: Ctx, head: boolean) {
  if (!readAllowed(req)) return new Response("Forbidden", { status: 403 });
  let abs: string;
  try {
    const [id, ...rest] = (await params).path, ref = await sessionRef(id);
    if (!ref) return new Response("No such session", { status: 404 });
    abs = await resolveMedia(ref, "/" + rest.join("/"));
  } catch (e) {
    if (e instanceof HttpError) return new Response(e.message, { status: e.status });
    throw e;
  }
  const st = await stat(abs).catch(() => null);
  if (!st?.isFile()) return new Response("Not found", { status: 404 });
  const size = st.size;
  const headers = new Headers({
    "Content-Type": TYPES[path.extname(abs).toLowerCase()] ?? "application/octet-stream",
    "Accept-Ranges": "bytes",
    "Cache-Control": "no-cache, no-transform",
    "Last-Modified": st.mtime.toUTCString(),
  });
  let start = 0, end = size - 1, status = 200;
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get("range")?.trim() ?? "");
  if (m && (m[1] || m[2])) {
    if (m[1]) {
      start = +m[1];
      if (m[2]) end = Math.min(+m[2], size - 1);
    } else start = Math.max(0, size - +m[2]);
    if (start > end) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    status = 206;
    headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
  }
  headers.set("Content-Length", String(Math.max(0, end - start + 1)));
  if (head || size === 0) return new Response(null, { status, headers });
  if (req.signal.aborted) return new Response(null, { status: 499 }); // gone already: no file to open, none to leak
  const file = fs.createReadStream(abs, { start, end, highWaterMark: 1 << 20 });
  req.signal.addEventListener("abort", () => file.destroy(), { once: true });
  return new Response(Readable.toWeb(file) as ReadableStream, { status, headers });
}

export const GET = (req: Request, ctx: Ctx) => serve(req, ctx, false);
export const HEAD = (req: Request, ctx: Ctx) => serve(req, ctx, true);
