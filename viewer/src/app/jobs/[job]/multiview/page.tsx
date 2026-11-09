import { connection } from "next/server";
import { Suspense } from "react";
import { MultiView } from "@/components/multi/MultiView";
import { firstParam, fromParam, numParam } from "@/lib/format";
import { STATUS_WORD } from "@/lib/job";
import type { Session } from "@/lib/session";
import { loadSession, readCheck, requireSession, type SessionRef } from "@/server/camrig";
import { readTranscript } from "@/server/data";
import { runState } from "@/server/run";

export const metadata = { title: "Multi-view" };

export default function MultiPage({ params, searchParams }: PageProps<"/jobs/[job]/multiview">) {
  return (
    <Suspense fallback={<MultiSkeleton />}>
      <Multi params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Multi({ params, searchParams }: Pick<PageProps<"/jobs/[job]/multiview">, "params" | "searchParams">) {
  const [{ job: rawJob }, q] = await Promise.all([params, searchParams]);
  const job = fromParam(rawJob);
  await connection();
  const ref = await requireSession(job), session = await loadSession(ref, true);
  if (!session?.cameras.length) {
    const check = await readCheck(ref, "clock");
    return (
      <main className="page" data-screen="multi-none">
        <section className="card box">
          <h1>Multi-view</h1>
          <p className="note" data-not-yet="clock">
            No session clock yet. It is made by the <b>clock</b> step, which {check?.status ? `last ended ${STATUS_WORD[check.status] ?? check.status}` : "has not run yet"}. Run it from the session&apos;s overview.
          </p>
        </section>
      </main>
    );
  }
  const [{ density, counts }, run] = await Promise.all([cues(ref, session), runState(ref)]);
  return (
    <>
      <MultiView job={job} session={session} density={density} cueCounts={counts} initialClock={numParam(q.c)} initialPart={firstParam(q.part) ?? null} nav={crypto.randomUUID()} run={run} />
      <ClockFacts s={session} />
    </>
  );
}

// Per camera with sound: cues per minute of the clock; per part ("cam/part"): its cue count, null without a transcript.
async function cues(ref: SessionRef, s: Session) {
  const first = Math.floor(s.clock.min / 60), bins = Math.floor(s.clock.max / 60) - first + 1, density: Record<string, number[]> = {}, counts: Record<string, number | null> = {};
  for (const cam of s.cameras.filter(c => c.sound)) {
    const n = new Array<number>(bins).fill(0);
    const all = await Promise.all(cam.parts.map(p => readTranscript(`${ref.id}/${cam.key}/${p.key}`).then(t => ({ p, t }), () => null)));
    for (const r of all) {
      if (!r) continue;
      counts[`${cam.key}/${r.p.key}`] = r.t.exists ? r.t.cues.length : null;
      for (const c of r.t.cues) {
        const i = Math.floor((r.p.clock_from + (c.startMs / 1000) * r.p.fps * r.p.s_per_frame) / 60) - first;
        if (i >= 0 && i < bins) n[i]++;
      }
    }
    density[cam.key] = n;
  }
  return { density, counts };
}

function ClockFacts({ s }: { s: Session }) {
  const sources = new Map<string, number>();
  for (const p of s.cameras.flatMap(c => c.parts)) sources.set(p.source || "unknown", (sources.get(p.source || "unknown") ?? 0) + 1);
  return (
    <section className="below" aria-label="How the clock was made">
      <div className="card box" data-clock-facts>
        <h2>One clock</h2>
        <p className="note">Every frame of every camera on one clock. {s.clock.zero ? `Clock zero: ${s.clock.zero}.` : ""} Pick a moment and each camera shows its frame nearest to it; cameras at different frame rates can differ by a few hundredths of a second. While playing, the cameras are lined up again whenever they drift apart by more than a quarter of a second.</p>
        <p className="note">The reference camera is <b>{s.clock.reference}</b>. {s.clock.existing ? <>This session keeps a clock made earlier, from <span className="mono">{s.clock.existing}</span>.</> : null} How the parts were placed: {[...sources].map(([k, n]) => `${n} by ${SOURCE[k] ?? k}`).join(", ")}.</p>
        {s.off.length > 0 && <p className="note" data-off-clock>Not on this clock: {s.off.join(", ")}.</p>}
        {Object.keys(s.clock.evidence).length > 0 && (
          <details className="fold" data-clock-details>
            <summary>What the clock step measured</summary>
            <ul className="numbers">{Object.entries(s.clock.evidence).map(([k, v]) => <li key={k}><b>{k}</b>: {String(v)}</li>)}</ul>
          </details>
        )}
      </div>
    </section>
  );
}

const SOURCE: Record<string, string> = {
  sound: "sound shared with other cameras", chained: "following the part before", motion: "picture motion against the sound cameras",
  reference: "being the reference camera", manual: "hand", unplaced: "nothing yet (left out of the clock, shown at a first guess)", stopwatch: "the stopwatch", audio: "sound", existing: "the clock made earlier",
};

function MultiSkeleton() {
  return (
    <div className="multi" aria-busy="true">
      <div className="panels">{[0, 1, 2].map(i => <div key={i} className="panel card"><div className="panel-head" /><div className="pv" /><div style={{ height: 44 }} /></div>)}</div>
      <div className="card" style={{ height: 196 }} />
      <div className="card strip" />
    </div>
  );
}
