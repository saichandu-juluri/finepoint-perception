"use client";
// The app header: brand, breadcrumb, the Library / Single view / Multi-view tabs and the keyboard shortcuts sheet.
import { useEffect, useRef, useState } from "react";
import { Icon, Logo } from "./Icon";
import { NavLink, isTyping } from "./nav";

export type Crumb = { label: string; href?: string; mono?: boolean };
export type Tab = "library" | "single" | "multi" | null;

export const LAST_VIDEO_KEY = "camrig:last-video";

export function TopBar({ crumbs, tab, singleHref, multiHref }: { crumbs: Crumb[]; tab: Tab; singleHref: string | null; multiHref: string | null }) {
  const [single, setSingle] = useState(singleHref);
  const keys = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    try {
      const last = localStorage.getItem(LAST_VIDEO_KEY);
      if (last && tab !== "single") setSingle(last);
    } catch {}
  }, [tab]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?" || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      e.preventDefault();
      const d = keys.current;
      if (d && !d.open) d.showModal();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const tabLink = (id: Exclude<Tab, null>, label: string, icon: string, href: string | null) =>
    href ? (
      <NavLink className="tab" href={href} aria-current={tab === id ? "page" : undefined}>
        <Icon name={icon} />{label}
      </NavLink>
    ) : (
      <span className="tab" aria-disabled="true" title="Nothing to open yet"><Icon name={icon} />{label}</span>
    );

  return (
    <header className="topbar">
      <NavLink className="brand" href="/"><Logo /><span>FinePoint Perception</span></NavLink>
      <nav className="crumbs" aria-label="Breadcrumb">
        {crumbs.map((c, i) => (
          <span key={i} className="crumb">
            {i > 0 && <Icon name="chevR" className="sep" />}
            {c.href && i < crumbs.length - 1
              ? <NavLink href={c.href} className={c.mono ? "mono" : undefined}>{c.label}</NavLink>
              : <span className={`cur${c.mono ? " mono" : ""}`} aria-current="page">{c.label}</span>}
          </span>
        ))}
      </nav>
      <nav className="tabs" aria-label="Views">
        {tabLink("library", "Library", "library", "/")}
        {tabLink("single", "Single view", "single", single)}
        {tabLink("multi", "Multi-view", "views", multiHref)}
      </nav>
      <button type="button" className="btn ghost sm keys-btn" onClick={() => keys.current?.showModal()}>
        <Icon name="kbd" />Shortcuts<kbd>?</kbd>
      </button>
      <dialog ref={keys} className="keys" aria-label="Keyboard shortcuts" onClick={e => e.target === e.currentTarget && e.currentTarget.close()}>
        <div className="keys-in">
          <h2>Keyboard shortcuts</h2>
          <div className="keys-cols">
            <section>
              <h3>Single view</h3>
              <dl>
                <dt><kbd>Space</kbd> <kbd>K</kbd></dt><dd>Play or pause</dd>
                <dt><kbd>J</kbd> <kbd>L</kbd></dt><dd>Back or forward 5 s</dd>
                <dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>One frame (with <kbd>⇧</kbd>: 1 s)</dd>
                <dt><kbd>N</kbd></dt><dd>New cue at the playhead</dd>
                <dt><kbd>Esc</kbd></dt><dd>Stop editing the cue</dd>
                <dt><kbd>C</kbd></dt><dd>Captions on or off</dd>
                <dt><kbd>/</kbd></dt><dd>Search the transcript</dd>
                <dt><kbd>⌘S</kbd></dt><dd>Save the .srt and the .txt</dd>
                <dt><kbd>⌘Z</kbd> <kbd>⇧⌘Z</kbd></dt><dd>Undo, redo</dd>
              </dl>
              <h3>While typing in a cue</h3>
              <dl>
                <dt><kbd>Return</kbd></dt><dd>Keep the text, open the next cue</dd>
                <dt><kbd>⇧Return</kbd></dt><dd>New line</dd>
                <dt><kbd>Esc</kbd></dt><dd>Undo the typing and stop editing</dd>
                <dt><kbd>⌥K</kbd> <kbd>⌥J</kbd> <kbd>⌥L</kbd></dt><dd>Play or pause, back, forward</dd>
              </dl>
            </section>
            <section>
              <h3>Multi-view</h3>
              <dl>
                <dt><kbd>Space</kbd></dt><dd>Play or pause all cameras</dd>
                <dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>One step (with <kbd>⇧</kbd>: 1 s)</dd>
                <dt><kbd>Return</kbd></dt><dd>Go to the time typed in the clock</dd>
              </dl>
              <h3>Anywhere</h3>
              <dl>
                <dt><kbd>?</kbd></dt><dd>This sheet</dd>
                <dt><kbd>Esc</kbd></dt><dd>Close it</dd>
              </dl>
            </section>
          </div>
          <form method="dialog"><button className="btn secondary">Close</button></form>
        </div>
      </dialog>
    </header>
  );
}

export function TopBarSkeleton() {
  return (
    <header className="topbar">
      <span className="brand"><Logo /><span>FinePoint Perception</span></span>
      <nav className="crumbs" aria-label="Breadcrumb"><span className="sk sk-line" style={{ width: 220 }} /></nav>
    </header>
  );
}
