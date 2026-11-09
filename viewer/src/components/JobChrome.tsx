"use client";
// Header and step navigation of a job, shared by all its pages. Which page is open comes from the address.
import { usePathname } from "next/navigation";
import type { Step } from "@/lib/job";
import { Icon } from "./Icon";
import { NavLink } from "./nav";
import { TopBar, type Crumb } from "./TopBar";

export function JobChrome({ job, title, steps, singleHref, multiHref }: {
  job: string; title: string; steps: Step[]; singleHref: string | null; multiHref: string | null;
}) {
  const base = `/jobs/${encodeURIComponent(job)}`, path = usePathname();
  const [seg = "", ...rest] = path.startsWith(base) ? path.slice(base.length + 1).split("/") : [];
  const file = seg === "watch" ? rest.map(decodeURIComponent) : null; // [camera, part] or [camera, part, "depth"]
  const crumbs: Crumb[] = [{ label: "Library", href: "/" }, { label: title, href: base }];
  if (file) crumbs.push({ label: `${file[0]} · ${file[1]}${file[2] === "depth" ? ", depth" : ""}` });
  else if (seg) crumbs.push({ label: steps.find(s => s.key === seg)?.name ?? seg });
  const current = seg || "overview";
  return (
    <>
      <TopBar crumbs={crumbs} tab={file ? "single" : seg === "multiview" ? "multi" : "library"} singleHref={singleHref} multiHref={multiHref} />
      {steps.length > 0 && !file && (
        <nav className="stepnav" aria-label="Steps of this session">
          <ol>
            {steps.map(s => (
              <li key={s.key}>
                <NavLink href={s.href} className={`step ${s.done ? "is-done" : "is-todo"}`} aria-current={s.key === current ? "page" : undefined}>
                  <span className="step-dot">{s.done ? <Icon name="check" /> : s.n}</span>
                  <span className="step-txt"><span className="step-name">{s.n} {s.name}</span><span className="step-st">{s.state}</span></span>
                </NavLink>
              </li>
            ))}
          </ol>
        </nav>
      )}
    </>
  );
}
