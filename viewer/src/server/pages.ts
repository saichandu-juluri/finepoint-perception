// Reads the pages share. Server only.
import { jobSteps } from "@/lib/job";
import { readChecks, requireSession } from "./camrig";

export async function chrome(id: string) {
  const ref = await requireSession(id);
  const statuses = Object.fromEntries(Object.entries(await readChecks(ref)).map(([k, c]) => [k, c?.status ?? null]));
  return { ref, steps: jobSteps(id, statuses) };
}
