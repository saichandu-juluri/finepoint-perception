import { connection } from "next/server";
import { Suspense } from "react";
import { Gantt } from "@/components/Gantt";
import { Icon } from "@/components/Icon";
import { NavLink } from "@/components/nav";
import { RunPanel } from "@/components/RunPanel";
import { UploadPanel } from "@/components/UploadPanel";
import { fileName, fmtMin, fromParam, jobHref, plural, watchHref } from "@/lib/format";
import type { Session } from "@/lib/session";
import { fmtClock } from "@/lib/srt";
import { loadSession, project, readCameras, type CamerasJson, type SessionRef } from "@/server/camrig";
import { transcriptCounts } from "@/server/data";
import { chrome } from "@/server/pages";
import { runState } from "@/server/run";
import { uploadState } from "@/server/upload";

export const metadata = { title: "Overview" };

export default function JobPage({ params }: PageProps<"/jobs/[job]">) {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Overview params={params} />
    </Suspense>
  );
}

async function Overview({ params }: { params: Promise<{ job: string }> }) {
  const job = fromParam((await params).job);
  await connection();
  const { ref } = await chrome(job);
  const [p, cams, s, run, upload] = await Promise.all([project(ref).catch(e => ({ ok: false as const, error: String(e.message) })), readCameras(ref), loadSession(ref), runState(ref), uploadState(ref, false)]);
  const parts = cams?.cameras.reduce((n, c) => n + c.parts.length, 0) ?? 0;
  return (
    <main className="page" data-screen="overview">
      <header className="head-row">
        <div className="page-title">
          <div className="eyebrow">Session</div>
          <h1>{p.ok ? p.name : job}</h1>
          <div className="folder mono">{ref.dir}</div>
          {p.ok ? (
            <ul className="facts">
              <li><Icon name="video" /><b>{p.cameras.length}</b> cameras</li>
              <li><Icon name="folder" /><b>{cams ? parts : p.cameras.reduce((n, c) => n + c.videos, 0)}</b> recordings</li>
              {s && <li><Icon name="clock" />Session clock <b className="mono">{fmtClock(s.clock.min, 0)}</b> to <b className="mono">{fmtClock(s.clock.max, 0)}</b> · {Math.round((s.clock.max - s.clock.min) / 60)} min</li>}
            </ul>
          ) : <p className="note bad-note" data-project-error>This project file can&apos;t be read: {p.error}</p>}
        </div>
        <NavLink className="btn primary lg" href={jobHref(job, "multiview")} style={{ marginTop: 26 }}><Icon name="views" />Open multi-view</NavLink>
      </header>

      <RunPanel job={job} initial={run} />
      <UploadPanel job={job} initial={upload} cloudUrl={process.env.CAMRIG_CLOUD_URL ?? ""} />

      <section className="sec-head">
        <div>
          <h2>Recordings</h2>
          <p>What came off the cameras, as the cameras step read it. The recordings are only read, never changed. Open a file to watch it and correct its transcript.</p>
        </div>
      </section>
      {s && s.cameras.length > 0 && <div className="card gantt-card"><Gantt job={job} s={s} /></div>}
      {s && s.off.length > 0 && <p className="note" data-off-clock>Not on the clock: {s.off.join(", ")}. The clock step names why.</p>}
      {cams ? <Recordings job={job} ref_={ref} cams={cams} s={s} /> : <p className="card box muted" data-no-cameras>No list of recordings yet: the cameras step makes it. Run it above.</p>}
    </main>
  );
}

async function Recordings({ job, ref_, cams, s }: { job: string; ref_: SessionRef; cams: CamerasJson; s: Session | null }) {
  const counts = await transcriptCounts(ref_, cams);
  return cams.cameras.map(cam => {
    const depth = cam.kind === "depth", onClock = s?.cameras.find(c => c.key === cam.name);
    const seconds = cam.parts.reduce((n, p) => n + p.seconds, 0), fps = [...new Set(cam.parts.map(p => p.fps))].join(", ");
    const done = cam.parts.filter(p => counts[p.file]).length, heard = cam.parts.filter(p => p.sound).length;
    return (
      <section key={cam.name} className="card cam" data-cam={cam.name} aria-label={cam.name}>
        <header className="cam-head">
          <span className="cam-ico"><Icon name="video" /></span>
          <div><h3>{cam.name}{depth ? ", depth camera" : `, ${cam.kind} camera`}</h3><p>{plural(cam.parts.length, "file")} · {fmtMin(seconds)} · {fps} fps{depth ? " · colour and depth" : ""}{onClock ? "" : " · not on the clock"}</p></div>
          {!heard ? <span className="tag">No sound, so no transcript</span> : <span className="tag teal">{done} of {heard} with a transcript{heard < cam.parts.length ? ` · ${cam.parts.length - heard} without sound` : ""}</span>}
        </header>
        <div className="tbl-wrap">
          <table className="files">
            <thead><tr><th>Part</th><th>File</th><th>Length</th><th>On the session clock</th><th>Transcript</th><th><span className="sr">Open</span></th></tr></thead>
            <tbody>
              {cam.parts.map(p => {
                const cp = onClock?.parts.find(q => q.file === p.file), t = counts[p.file];
                return (
                  <tr key={p.key} data-file={p.file}>
                    <td className="c-part">{p.key}</td>
                    <td className="c-file">{fileName(p.file)}{p.bag && <span className="sub">from <span className="mono">{fileName(p.bag)}</span>{p.missing ? ` · ${p.missing} frames lost` : ""}</span>}</td>
                    <td className="c-num">{fmtClock(p.seconds, 0)}</td>
                    <td className="c-num">{cp ? <><span className="mono">{fmtClock(cp.clock_from, 2)}</span> <span className="muted">to</span> <span className="mono">{fmtClock(cp.clock_to, 2)}</span></> : <span className="muted">not on the clock</span>}</td>
                    <td>{!p.sound ? <span className="tr-none">No sound</span> : !t ? <span className="tr-none">None yet</span>
                      : <span className="tr-ok"><Icon name="doc" />{plural(t.cues, "cue")}{t.source === "edited" ? ", edited" : ""}</span>}</td>
                    <td>
                      <span className="row-acts">
                        {p.depth_video && <NavLink className="btn ghost sm" href={watchHref(job, cam.name, p.key, "", true)}>Depth</NavLink>}
                        <NavLink className="btn secondary sm" href={watchHref(job, cam.name, p.key)} aria-label={`Open ${cam.name} ${p.key}`}>Open<Icon name="chevR" /></NavLink>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    );
  });
}

function PageSkeleton() {
  return (
    <main className="page" aria-busy="true">
      <div className="page-title"><span className="sk sk-line" style={{ width: 140 }} /><span className="sk" style={{ width: 460, height: 34, marginTop: 10 }} /></div>
      <div className="card" style={{ height: 170 }} />
      <div className="card" style={{ height: 420 }} />
    </main>
  );
}
