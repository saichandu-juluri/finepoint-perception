// GET ?path=: the folder as typed, checked on this computer: its subfolders to browse into and the cameras it would give.
import { answer } from "@/server/data";
import { browse } from "@/server/newproject";

export const GET = (req: Request) => answer(req, () => browse(new URL(req.url).searchParams.get("path")));
