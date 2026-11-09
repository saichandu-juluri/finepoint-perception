import { notFound } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";
import { SingleView } from "@/components/single/SingleView";
import { fileName, firstParam, fromParam, jobHref, mediaUrl, numParam, watchHref } from "@/lib/format";
import { fmtClock } from "@/lib/srt";
import { findPart, loadSession, requireSession } from "@/server/camrig";
import { readTranscript } from "@/server/data";

export const metadata = { title: "Single view" };

export default function WatchPage({ params, searchParams }: PageProps<"/jobs/[job]/watch/[...file]">) {
  return (
    <Suspense fallback={<SingleSkeleton />}>
      <Watch params={params} searchParams={searchParams} />
    </Suspense>
  );
}

// /jobs/<session>/watch/<camera>/<part>[/depth]
async function Watch({ params, searchParams }: Pick<PageProps<"/jobs/[job]/watch/[...file]">, "params" | "searchParams">) {
  const [{ job: rawJob, file }, q] = await Promise.all([params, searchParams]);
  await connection();
  const job = fromParam(rawJob), [cam, key, extra, more] = file.map(fromParam);
  if (!cam || !key || more || (extra && extra !== "depth")) notFound();
  const ref = await requireSession(job), hit = await findPart(ref, cam, key);
  const depth = extra === "depth", abs = depth ? hit?.depth : hit?.file;
  if (!hit || !abs) notFound();
  const ref3 = `${job}/${cam}/${key}`;
  const [transcript, s] = await Promise.all([hit.sound && !depth ? readTranscript(ref3) : null, loadSession(ref)]);
  const part = s?.cameras.find(c => c.key === cam)?.parts.find(p => p.file === hit.file) ?? null;
  const index = hit.parts.indexOf(key), name = fileName(abs);
  const link = (i: number) => (i >= 0 && i < hit.parts.length ? { href: watchHref(job, cam, hit.parts[i], "", depth), label: `${cam} · ${hit.parts[i]}` } : null);
  const c = numParam(q.c), t = numParam(q.t);
  const srtName = name.replace(/\.[^.]+$/, ".srt");
  return (
    <SingleView
      key={`${cam}/${key}/${extra ?? ""}`}
      title={`${cam}${hit.kind === "depth" ? ", depth camera" : ""}`}
      tag={`${key}${hit.kind === "depth" ? (depth ? ", depth" : ", colour") : ""}`}
      sub={[name, fmtClock(hit.frames / hit.fps, 2), `${hit.fps} fps`, part ? `Session clock ${fmtClock(part.clock_from, 2)} to ${fmtClock(part.clock_to, 2)}` : "Not on the session clock"]}
      fps={hit.fps}
      noSound={!hit.sound || depth}
      clock={part ? { from: part.clock_from, rate: part.fps * part.s_per_frame, multi: jobHref(job, "multiview") } : null}
      pager={{ prev: link(index - 1), next: link(index + 1), text: `${index + 1} of ${hit.parts.length}` }}
      videoPath={ref3}
      src={mediaUrl(job, abs)}
      srtName={srtName}
      txtName={srtName.replace(/\.srt$/, ".txt")}
      saveTo={`transcripts/${cam}/`}
      transcript={transcript ? { cues: transcript.cues, mtimeMs: transcript.mtimeMs, exists: transcript.exists, skipped: transcript.skipped } : { cues: [], mtimeMs: null, exists: false, skipped: 0 }}
      source={transcript?.source ?? null}
      startT={t}
      openEdit={firstParam(q.edit) === "1"}
      nav={crypto.randomUUID()}
      back={c != null && part ? { href: `${jobHref(job, "multiview")}?c=${c.toFixed(4)}`, label: `Back to multi-view at ${fmtClock(c, 2)}` } : null}
    />
  );
}

function SingleSkeleton() {
  return (
    <div className="single" aria-busy="true">
      <div className="sv-head"><div><span className="sk" style={{ width: 360, height: 26 }} /><span className="sk sk-line" style={{ width: 520, marginTop: 10 }} /></div></div>
      <div className="single-grid">
        <div className="player card"><div className="video-box" /><div style={{ height: 210 }} /></div>
        <div className="editor card" style={{ padding: 16, gap: 12 }}>
          <span className="sk sk-line" style={{ width: 120 }} />
          <span className="sk" style={{ height: 38 }} />
          {Array.from({ length: 8 }, (_, i) => <span key={i} className="sk" style={{ height: 44, opacity: 1 - i * 0.1 }} />)}
        </div>
      </div>
    </div>
  );
}
