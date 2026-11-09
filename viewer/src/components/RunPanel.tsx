"use client";
// The steps of a session and the Run button: POST /api/run starts `camrig.run` for every step, GET is asked every 2 s
// while it runs, DELETE cancels. When a run ends the page reloads its server parts (the recordings, the clock), without
// a new Back step.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { RunState, StepState } from "@/server/run";
import { Icon } from "./Icon";
import { ItemList } from "./Items";
import { StatusPill } from "./Status";

const STEP_WORDS: Record<string, string> = { cameras: "What each camera recorded", scan: "Where the board is seen", clock: "All cameras on one clock" };

export function useRun(job: string, initial: RunState | null) {
  const [st, setSt] = useState<RunState | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const was = useRef(initial?.job?.status === "running");
  const poll = useCallback(async () => {
    const j = await fetch(`/api/run?session=${encodeURIComponent(job)}`, { cache: "no-store" }).then(r => (r.ok ? (r.json() as Promise<RunState>) : null)).catch(() => null);
    if (!j) return;
    setSt(j);
    const now = j.job?.status === "running";
    if (was.current && !now) router.refresh();
    was.current = now;
  }, [job, router]);
  const running = st?.job?.status === "running";
  useEffect(() => {
    if (!running) return;
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, [running, poll]);
  useEffect(() => { poll(); }, [poll]);
  const call = async (method: "POST" | "DELETE", only?: string | null) => {
    setError(null);
    const r = await fetch(method === "POST" ? "/api/run" : `/api/run?session=${encodeURIComponent(job)}`, {
      method, headers: { "Content-Type": "application/json" }, body: method === "POST" ? JSON.stringify({ session: job, only: only || null }) : undefined,
    }).catch(() => null);
    const j = await r?.json().catch(() => null);
    if (!r?.ok) setError(j?.error ?? "The server did not answer.");
    else { setSt(j); was.current = j.job?.status === "running"; }
  };
  // only: one step (Multi-view reruns the clock); left out, every step
  return { st, error, running, start: (only?: string | null) => call("POST", only), cancel: () => call("DELETE"), poll };
}

const ago = (ms: number) => { const s = Math.max(0, Math.round(ms / 1000)); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`; };

export function RunPanel({ job, initial }: { job: string; initial: RunState }) {
  const run = useRun(job, initial), st = run.st ?? initial, j = st.job;
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  const what = j?.only ? `the ${j.only} step` : "every step";
  const msg = run.error ? run.error
    : j?.status === "running" ? `Running ${what} for ${ago(st.now - j.started)}. You can leave this page; the run goes on.`
    : j?.status === "done" ? `The run of ${what} finished after ${ago((j.ended ?? st.now) - j.started)}.`
    : j?.status === "cancelled" ? "The run was cancelled. A step that was cut off runs again next time."
    : j?.status === "failed" ? `The run stopped: ${j.error ?? "no reason given"}`
    : st.busy ? "Another session's steps are running on this computer." : "";
  return (
    <section className="card box" aria-labelledby="h-steps" data-run data-ready={ready || undefined}>
      <div className="sec-head">
        <div>
          <h2 id="h-steps">Steps</h2>
          <p>Run lines up the cameras: the cameras step reads what each camera recorded, the scan step finds where the board is seen, and the clock step puts all cameras on one clock. Each step checks its own result: <b>PASS</b> fine, <b>FLAG</b> usable but look at the place named, <b>FAIL</b> not usable, <b>NOT TESTED</b> it could not check. A step whose inputs did not change is skipped.</p>
        </div>
      </div>
      <div className="run-bar">
        {run.running
          ? <button type="button" className="btn secondary" onClick={run.cancel} data-run-cancel><Icon name="x" />Cancel</button>
          : <button type="button" className="btn primary" onClick={() => run.start()} disabled={st.busy} data-run-start><Icon name="play" />Run</button>}
        <span className={`run-msg${run.error || j?.status === "failed" ? " is-err" : ""}`} data-run-msg={j?.status ?? ""}>{msg}</span>
      </div>
      <div className="tbl-wrap" style={{ marginTop: 12 }}>
        <table className="data steps-tbl" data-steps>
          <thead><tr><th>Step</th><th>Status</th><th>Last run</th><th>What it found</th></tr></thead>
          <tbody>{st.steps.map(s => <StepRow key={s.name} s={s} live={run.running} />)}</tbody>
        </table>
      </div>
    </section>
  );
}

function StepRow({ s, live }: { s: StepState; live: boolean }) {
  const look = s.items.filter(i => i.status !== "pass"), ok = s.items.length - look.length;
  const pill = s.now === "running" ? <StatusPill status="running" label="RUNNING" /> : s.now === "waiting" ? <><StatusPill status={s.status} /> <span className="muted">waits its turn</span></> : <StatusPill status={s.status} />;
  return (
    <tr className="step-row" data-step={s.name} data-step-status={s.status ?? ""} data-step-now={s.now ?? ""}>
      <td>{s.name}<div className="muted" style={{ fontWeight: 400, fontSize: 13.5 }}>{STEP_WORDS[s.name] ?? ""}</div></td>
      <td>{pill}{s.now === "skipped" && <div className="muted" style={{ fontSize: 13.5 }}>unchanged, skipped</div>}
        {s.stale.length > 0 && s.now !== "running" && <div className="run-msg is-err" style={{ fontSize: 13.5 }} data-step-stale={s.stale.join(",")}>
          Out of date: it builds on {s.stale.join(", ")}, which ran again after it or must run again. Run to bring it up to date.</div>}</td>
      <td className="mono" style={{ whiteSpace: "nowrap" }}>{s.started ? s.started.replace("T", " ") : ""}{s.seconds != null && <div className="muted">{s.seconds} s</div>}</td>
      <td>
        {!s.status && s.now !== "running" && <span className="muted">Not run yet.</span>}
        {look.length > 0 && <ItemList items={look} />}
        {ok > 0 && <details className="fold"><summary>{ok} {ok === 1 ? "check passes" : "checks pass"}</summary><ItemList items={s.items.filter(i => i.status === "pass")} /></details>}
        {s.log.length > 0 && (
          <details className="fold" open={live && s.now === "running"} data-log>
            <summary>End of its log</summary>
            <pre className="log">{s.log.join("\n")}</pre>
          </details>
        )}
      </td>
    </tr>
  );
}
