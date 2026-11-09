// GET ?file=<video>: one picture from the video, as JPEG, for the camera cards of New project.
import { HttpError, fromThisApp, ownPage, readAllowed } from "@/server/data";
import { picture } from "@/server/newproject";

export async function GET(req: Request) {
  if (!fromThisApp(req) || !readAllowed(req) || !ownPage(req)) return new Response("Forbidden", { status: 403 });
  try {
    const jpeg = await picture(new URL(req.url).searchParams.get("file") ?? "");
    return new Response(new Uint8Array(jpeg), { headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=600" } });
  } catch (e) {
    if (e instanceof HttpError) return new Response(e.message, { status: e.status });
    throw e;
  }
}
