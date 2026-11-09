import { connection } from "next/server";
import { Suspense } from "react";
import { Icon } from "@/components/Icon";
import { NavLink } from "@/components/nav";
import { StatusPill } from "@/components/Status";
import { TopBar, TopBarSkeleton } from "@/components/TopBar";
import { jobHref } from "@/lib/format";
import { listSessions, projects, readChecks, ROOTS } from "@/server/camrig";
import { uploadBadge } from "@/server/upload";

export default function LibraryPage() {
  return (
    <div className="shell">
      <Suspense fallback={<LibrarySkeleton />}>
        <Library />
      </Suspense>
    </div>
  );
}

async function Library() {
  await connection();
  const refs = await listSessions();
  const [infos, checks, uploads] = await Promise.all([projects(refs.map(r => r.toml)), Promise.all(refs.map(readChecks)), Promise.all(refs.map(uploadBadge))]);
  return (
    <>
      <TopBar crumbs={[{ label: "Library" }]} tab="library" singleHref={null} multiHref={refs[0] ? jobHref(refs[0].id, "multiview") : null} />
      <main className="page" data-screen="library">
        <header className="head-row">
          <div className="page-title">
            <div className="eyebrow">Library</div>
            <h1>Sessions</h1>
            <p className="lead" style={{ marginTop: 8 }}>Every session folder on this computer: a folder with a project.toml. Open one to see what each step found, run the steps, watch the cameras on one clock and correct the transcripts.</p>
          </div>
          <NavLink className="btn primary lg" href="/new" style={{ marginTop: 26 }} data-new-project><Icon name="plus" />New project</NavLink>
        </header>
        <p className="note" data-roots>Sessions are kept in {ROOTS.map((r, i) => <span key={r}>{i > 0 && ", "}<span className="mono">{r}</span></span>)}.</p>

        <section aria-labelledby="h-sessions" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {refs.length === 0 && <p className="card box muted" data-empty-library>No session here yet. Make one with New project.</p>}
          {refs.map((r, i) => {
            const p = infos[r.toml], c = checks[i], up = uploads[i];
            return (
              <article key={r.id} className="card session-card" data-job={r.id}>
                <div>
                  <div className="eyebrow">Session</div>
                  <h2><NavLink href={jobHref(r.id)}>{p.ok ? p.name : r.id}</NavLink></h2>
                  <div className="folder mono">{r.dir}</div>
                  {p.ok ? (
                    <ul className="facts">
                      <li><Icon name="video" /><b>{p.cameras.length}</b> cameras: {p.cameras.map(c => `${c.name}${c.kind === "depth" ? " (depth camera)" : ""}`).join(", ")}</li>
                      <li><Icon name="folder" /><b>{p.cameras.reduce((n, c) => n + c.videos, 0)}</b> recordings</li>
                      {p.clock_existing && <li><Icon name="clock" />Keeps a clock made earlier</li>}
                      {up && <li><span className={`tag up-${up.tone}`} data-upload-badge={up.text}><Icon name="upload" />{up.text}</span></li>}
                    </ul>
                  ) : <p className="note bad-note" data-project-error>This project file can&apos;t be read: {p.error}</p>}
                </div>
                <div className="sc-actions">
                  <NavLink className="btn primary lg" href={jobHref(r.id)}>Open<Icon name="arrowR" /></NavLink>
                  <NavLink className="btn secondary" href={jobHref(r.id, "multiview")}><Icon name="views" />Multi-view</NavLink>
                </div>
                <ol className="pills" aria-label="Steps">
                  {Object.entries(c).map(([name, ch]) => <li key={name}><span className="pill-name">{name}</span><StatusPill status={ch?.status ?? null} /></li>)}
                </ol>
              </article>
            );
          })}
        </section>
      </main>
    </>
  );
}

function LibrarySkeleton() {
  return (
    <>
      <TopBarSkeleton />
      <main className="page" aria-busy="true">
        <div className="page-title"><span className="sk sk-line" style={{ width: 90 }} /><span className="sk" style={{ width: 420, height: 34, marginTop: 10 }} /></div>
        <div className="card" style={{ height: 290 }} />
      </main>
    </>
  );
}
