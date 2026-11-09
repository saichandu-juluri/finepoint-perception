// POST {folder, name, board: {columns, rows, square_mm, marker_mm}, cameras: [{folder, name}]}: writes the session's
// project.toml, checked by camrig's own reader; {id, toml}.
import { answer } from "@/server/data";
import { create, type NewProject } from "@/server/newproject";

export const POST = (req: Request) => answer(req, async () => create((await req.json().catch(() => null)) as NewProject), true);
