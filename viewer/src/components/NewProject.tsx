"use client";
// New project: the folder path, checked by the server as it is typed, a browser of its subfolders, one card per camera
// subfolder (a picture, its files and length) where the person confirms the names, the board, and Create.
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fmtBytes, plural } from "@/lib/format";
import { fmtClock } from "@/lib/srt";
import type { Browse } from "@/server/newproject";
import { Icon } from "./Icon";

export function NewProject({ root, home }: { root: string; home: string }) {
  const [typed, setTyped] = useState(home);
  const [b, setB] = useState<Browse | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [use, setUse] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [board, setBoard] = useState({ columns: 5, rows: 7, square_mm: 20, marker_mm: 14 });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const ctl = new AbortController();
    const id = setTimeout(() => {
      fetch(`/api/folders?path=${encodeURIComponent(typed)}`, { cache: "no-store", signal: ctl.signal }).then(r => r.json()).then((j: Browse) => {
        setB(j);
        setNames(Object.fromEntries(j.cameras.map(c => [c.folder, c.name])));
        setUse(Object.fromEntries(j.cameras.map(c => [c.folder, true])));
        setName(j.path.split("/").filter(Boolean).pop() ?? "");
      }).catch(() => {});
    }, 250);
    return () => { clearTimeout(id); ctl.abort(); };
  }, [typed]);

  const chosen = b?.cameras.filter(c => use[c.folder]) ?? [];
  const create = async () => {
    setErr(null); setBusy(true);
    const r = await fetch("/api/new", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: b!.path, name, board, cameras: chosen.map(c => ({ folder: c.folder, name: names[c.folder] })) }) }).catch(() => null);
    const j = await r?.json().catch(() => null);
    setBusy(false);
    if (!r?.ok) { setErr(j?.error ?? "The server did not answer."); return; }
    router.push(`/jobs/${encodeURIComponent(j.id)}`);
  };
  const num = (k: keyof typeof board) => (
    <label>{{ columns: "Squares across", rows: "Squares down", square_mm: "Square side, mm", marker_mm: "Marker side, mm" }[k]}
      <input type="number" min={k === "columns" || k === "rows" ? 2 : 0.1} step={k === "columns" || k === "rows" ? 1 : 0.1} value={board[k]} data-board={k}
        onChange={e => setBoard(x => ({ ...x, [k]: Number(e.target.value) }))} /></label>
  );

  return (
    <div className="np" data-ready={b ? true : undefined}>
      <section className="card box">
        <h2>1 The folder</h2>
        <div className="np-path">
          <input type="text" value={typed} onChange={e => setTyped(e.target.value)} aria-label="Folder path" spellCheck={false} data-np-path />
          {b?.parent && <button type="button" className="btn secondary" onClick={() => setTyped(b.parent!)} data-np-up><Icon name="arrowL" />Up</button>}
        </div>
        <p className={`note${b && !b.ok ? " bad-note" : ""}`} data-np-check={b ? (b.ok ? "ok" : "bad") : ""}>
          {!b ? "Looking…" : b.ok ? `${plural(b.cameras.length, "camera folder")} found here.` : b.error}
        </p>
        {!!b?.dirs.length && (
          <div className="np-dirs" data-np-dirs>
            {b.dirs.map(d => <button type="button" key={d.path} className="btn ghost sm" onClick={() => setTyped(d.path)}><Icon name="folder" />{d.name}</button>)}
          </div>
        )}
      </section>

      {b?.ok && (
        <>
          <section className="card box">
            <h2>2 The cameras</h2>
            <p className="note">Each subfolder is one camera. Name it after the label on the camera body; untick a folder that is no camera.</p>
            <div className="np-cams" data-np-cams>
              {b.cameras.map(c => (
                <div key={c.folder} className="np-cam" data-np-cam={c.name}>
                  {c.first ? <img src={`/api/picture?file=${encodeURIComponent(c.first)}`} alt={`A picture from ${c.first.split("/").pop()}`} />
                    : <div className="np-noimg">Depth camera recording (.bag): its picture shows once the cameras step has exported it.</div>}
                  <label><span><input type="checkbox" checked={!!use[c.folder]} onChange={e => setUse(u => ({ ...u, [c.folder]: e.target.checked }))} /> Use <span className="mono">{c.folder.split("/").pop()}</span></span></label>
                  <span className="muted" style={{ fontSize: 13.5 }}>{c.kind === "depth" ? "Depth camera" : "Action camera"} · {plural(c.files, c.kind === "depth" ? "recording" : "file")}{c.seconds != null ? ` · ${fmtClock(c.seconds, 0)}` : ""} · {fmtBytes(c.bytes)}</span>
                  <label>Name<input type="text" value={names[c.folder] ?? ""} onChange={e => setNames(n => ({ ...n, [c.folder]: e.target.value }))} data-np-name /></label>
                </div>
              ))}
            </div>
          </section>
          <section className="card box">
            <h2>3 The board</h2>
            <p className="note">The board you showed to the cameras: the scan step looks for a board with these numbers.</p>
            <div className="np-board">{num("columns")}{num("rows")}{num("square_mm")}{num("marker_mm")}</div>
          </section>
          <section className="card box">
            <h2>4 The session</h2>
            <div className="np-board">
              <label>Session name<input type="text" value={name} onChange={e => setName(e.target.value)} style={{ width: 320 }} data-np-session /></label>
              <button type="button" className="btn primary lg" onClick={create} disabled={busy || chosen.length < 2 || !name.trim()} data-np-create><Icon name="check" />Create the project</button>
            </div>
            <p className="note">The project file goes to <span className="mono">{root}/{name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")}/project.toml</span>. Create opens the session.</p>
            {err && <p className="note bad-note" data-np-error>{err}</p>}
          </section>
        </>
      )}
    </div>
  );
}
