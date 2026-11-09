// Every file of every camera on the session clock: one lane per camera, the parts the clock step flagged, 10-minute ticks.
import type { Session } from "@/lib/session";
import { flagMarks, shortLabel } from "@/lib/session";
import { fmtClock } from "@/lib/srt";
import { camName, jobHref, watchHref } from "@/lib/format";
import { Icon } from "./Icon";
import { NavLink } from "./nav";

export function Gantt({ job, s }: { job: string; s: Session }) {
  const { min, max } = s.clock, events = flagMarks(s), span = max - min, X = (t: number) => ((t - min) / span) * 100;
  // ponytail: label widths judged against a ~1150 px lane; container queries if this ever renders much narrower.
  const wide = (sec: number, px: number) => (sec / span) * 1150 >= px;
  const ticks: number[] = [];
  for (let m = Math.ceil(min / 600) * 10; m * 60 <= max; m += 10) ticks.push(m);
  return (
    <div className="gantt" role="group" aria-label="Every file on the session clock">
      <span />
      <div className="g-marks">
        {events.map(e => {
          const x = X(e.clock), side = x < 8 ? " l" : x > 92 ? " r" : "";
          return (
            <NavLink key={e.key} className={`g-ev${side}`} style={{ left: `${x}%` }} href={`${jobHref(job, "multiview")}?c=${e.clock.toFixed(4)}&part=${encodeURIComponent(e.key)}`} title={`The clock step flagged ${e.label}, which starts at ${fmtClock(e.clock, 2)}. Open the multi-view there.`} aria-label={`Open the multi-view at ${e.label}, flagged by the clock step`}>
              <Icon name="flag" />
            </NavLink>
          );
        })}
      </div>
      <div className="g-stack">{s.cameras.map(c => <span key={c.key} className="g-lab" title={c.label}>{camName(c)}</span>)}</div>
      <div className="g-stack">
        {s.cameras.map(c => (
          <div key={c.key} className="g-lane" data-cam={c.key}>
            {c.parts.map((p, i) => {
              const next = c.parts[i + 1], gap = next ? next.clock_from - p.clock_to : 0;
              return [
                <NavLink key={p.key} className="g-bar" href={watchHref(job, c.key, p.key)} data-file={p.file}
                  style={{ left: `${X(p.clock_from)}%`, width: `calc(${X(p.clock_to) - X(p.clock_from)}% - 2px)` }}
                  title={`${shortLabel(c)} ${p.key}: ${fmtClock(p.clock_from, 2)} to ${fmtClock(p.clock_to, 2)}. Open it.`}>
                  {wide(p.seconds, 54) ? p.key : ""}
                </NavLink>,
                gap > 60 && (
                  <span key={`${p.key}-gap`} className="g-gap" style={{ left: `${X(p.clock_to)}%`, width: `calc(${X(next.clock_from) - X(p.clock_to)}% - 2px)` }}
                    title={`Not recording from ${fmtClock(p.clock_to, 2)} to ${fmtClock(next.clock_from, 2)}`}>
                    {wide(gap, 150) ? `not recording, ${fmtClock(gap, 0)}` : ""}
                  </span>
                ),
              ];
            })}
          </div>
        ))}
        <div className="g-ovl">{events.map(e => <i key={e.key} className="g-evl" style={{ left: `${X(e.clock)}%` }} />)}</div>
      </div>
      <span />
      <div className="g-ruler" aria-hidden="true">{ticks.map(m => <span key={m} style={{ left: `${X(m * 60)}%` }}>{m}:00</span>)}</div>
    </div>
  );
}
