// GET ?path=: the folder as typed, checked on this computer: its subfolders to browse into and the cameras it would give.
import { answer, ownPage } from "@/server/data";
import { browse } from "@/server/newproject";

export const GET = (req: Request) => (ownPage(req) ? answer(req, () => browse(new URL(req.url).searchParams.get("path")))
  : Response.json({ error: "Forbidden" }, { status: 403 }));
