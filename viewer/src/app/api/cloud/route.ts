// GET: this computer's sign-in to the cloud (never the token). POST {url, email, password}: signs in; the password goes
// to camrig.cloud_account on its stdin only. DELETE: signs out. Each answers the sign-in as it is afterwards.
import { answer, HttpError } from "@/server/data";
import { cloudAccount, cloudStatus } from "@/server/upload";

const refreshed = <T,>(p: Promise<T>) => p.finally(() => cloudStatus(true));
export const GET = (req: Request) => answer(req, () => cloudStatus());
export const DELETE = (req: Request) => answer(req, async () => { await refreshed(cloudAccount(["signout"])); return cloudStatus(); }, true);
export const POST = (req: Request) => answer(req, async () => {
  const b = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const { url, email, password } = b ?? {};
  if (typeof url !== "string" || typeof email !== "string" || typeof password !== "string") throw new HttpError(400, "The server address, the email and the password are all needed.");
  await refreshed(cloudAccount(["signin"], JSON.stringify({ url, email, password })));
  return cloudStatus();
}, true);
