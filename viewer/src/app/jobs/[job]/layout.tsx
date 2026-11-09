import { connection } from "next/server";
import { Suspense } from "react";
import { JobChrome } from "@/components/JobChrome";
import { TopBarSkeleton } from "@/components/TopBar";
import { fromParam, jobHref, watchHref } from "@/lib/format";
import { project, readCameras } from "@/server/camrig";
import { chrome } from "@/server/pages";

export default function JobLayout({ children, params }: LayoutProps<"/jobs/[job]">) {
  return (
    <div className="shell">
      <Suspense fallback={<TopBarSkeleton />}>
        <Chrome params={params} />
      </Suspense>
      {children}
    </div>
  );
}

async function Chrome({ params }: { params: Promise<{ job: string }> }) {
  const job = fromParam((await params).job);
  await connection();
  const { ref, steps } = await chrome(job);
  const [p, cams] = await Promise.all([project(ref).catch(() => null), readCameras(ref)]);
  const first = cams?.cameras.find(c => c.parts.length);
  return (
    <JobChrome
      job={job}
      title={p?.ok ? p.name : job}
      steps={steps}
      singleHref={first ? watchHref(job, first.name, first.parts[0].key) : null}
      multiHref={jobHref(job, "multiview")}
    />
  );
}
