// A step's or an item's status in the words of docs/FORMATS.md: PASS, FLAG, FAIL, NOT TESTED, or not run yet.
import { STATUS_WORD } from "@/lib/job";

export const StatusPill = ({ status, label }: { status: string | null; label?: string }) => (
  <span className={`stp st-${status ?? "none"}`} data-status={status ?? "none"}>{label ?? (status ? STATUS_WORD[status] ?? status : "NOT RUN")}</span>
);
