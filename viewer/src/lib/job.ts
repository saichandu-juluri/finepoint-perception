// The pages of a session and what each one's step says, from the steps' check.json statuses. Pure.
import { jobHref } from "./format";

export type StepKey = "overview" | "multiview";
export type Step = { key: StepKey; n: number; name: string; state: string; done: boolean; href: string };

export const STATUS_WORD: Record<string, string> = { pass: "PASS", flag: "FLAG", fail: "FAIL", not_tested: "NOT TESTED" };
const word = (s: string | null | undefined) => (s ? STATUS_WORD[s] ?? s : "Not run yet");

// statuses: step name -> its check.json status, or null when the step has not run.
export function jobSteps(job: string, statuses: Record<string, string | null>): Step[] {
  const all = Object.values(statuses), ran = all.filter(Boolean).length, clock = statuses.clock ?? null;
  return [
    { key: "overview", n: 1, name: "Overview", done: ran === all.length && all.length > 0, href: jobHref(job), state: `${ran} of ${all.length} steps have run` },
    { key: "multiview", n: 2, name: "Multi-view", done: clock === "pass" || clock === "flag", href: jobHref(job, "multiview"), state: `clock step: ${word(clock)}` },
  ];
}
