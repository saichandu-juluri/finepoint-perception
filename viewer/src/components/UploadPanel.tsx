"use client";
// The session's upload to the cloud (docs/UPLOAD.md): one Upload button, one bar with the time left, Pause and Resume,
// and the files and what the cloud holds folded away. GET /api/upload is asked every 2 s while the uploader is sending.
// Not signed in, it shows the sign-in form; its server address starts as CAMRIG_CLOUD_URL (cloudUrl), else empty.
import { useCallback, useEffect, useRef, useState } from "react";
import { num } from "@/lib/format";
import type { UploadFile, UploadState } from "@/server/upload";
import { Icon } from "./Icon";

const GROUPS = ["original", "derived", "sync"] as const;
const LIVE = ["starting", "running", "pausing"];

const fileWord = (f: UploadFile, live: boolean) =>
  f.status === "done" ? "Done" : f.status === "failed" ? "Failed" : live && ["hashing", "uploading"].includes(f.status) ? "Uploading" : "Waiting";
const timeLeft = (s: number) => {
  const m = Math.max(1, Math.round(s / 60));
  return `About ${m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`} left`;
};

export function UploadPanel({ job, initial, cloudUrl }: { job: string; initial: UploadState; cloudUrl: string }) {
  const [st, setSt] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false); // a Start or Pause on its way: the buttons wait for it
  const inFlight = useRef<AbortController | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  const [misses, setMisses] = useState(0); // polls in a row the app did not answer
  const poll = useCallback(async () => {
    inFlight.current?.abort();
    const ac = (inFlight.current = new AbortController());
    const r = await fetch(`/api/upload?session=${encodeURIComponent(job)}`, { cache: "no-store", signal: ac.signal }).catch(() => null);
    if (ac.signal.aborted) return;
    setMisses(n => (r ? 0 : n + 1));
    const j = r?.ok ? await r.json().catch(() => null) : null;
    if (j && !ac.signal.aborted) setSt(j);
  }, [job]);
  const live = LIVE.includes(st.state);
  useEffect(() => {
    poll();
    if (!live) return () => inFlight.current?.abort();
    const id = setInterval(poll, 2000);
    return () => { clearInterval(id); inFlight.current?.abort(); };
  }, [live, poll]);
  const call = async (method: "POST" | "DELETE") => {
    setError(null); setSent(true);
    const r = await fetch(method === "POST" ? "/api/upload" : `/api/upload?session=${encodeURIComponent(job)}`, {
      method, headers: { "Content-Type": "application/json" }, body: method === "POST" ? JSON.stringify({ session: job }) : undefined,
    }).catch(() => null);
    const j = await r?.json().catch(() => null);
    setSent(false);
    if (!r?.ok) setError(j?.error ?? "The server did not answer.");
    else setSt(j);
  };

  const [form, setForm] = useState({ url: cloudUrl, email: initial.account.email ?? "", password: "" });
  const [signing, setSigning] = useState(false);
  const [signError, setSignError] = useState<string | null>(null);
  const signAc = useRef<AbortController | null>(null);
  useEffect(() => () => { signAc.current?.abort(); setSigning(false); }, []);
  const sign = async (method: "POST" | "DELETE") => {
    signAc.current?.abort();
    const ac = (signAc.current = new AbortController());
    setSignError(null); setSigning(true);
    const body = method === "POST" ? JSON.stringify(form) : undefined;
    const r = await fetch("/api/cloud", { method, headers: { "Content-Type": "application/json" }, body, signal: ac.signal }).catch(() => null);
    const j = await r?.json().catch(() => null);
    if (ac.signal.aborted) return;
    setSigning(false);
    if (!r?.ok) return setSignError(j?.error ?? "The server did not answer.");
    setForm(f => ({ ...f, password: "" }));
    poll();
  };
  const field = (k: keyof typeof form, label: string, type: string, auto: string, placeholder?: string) => (
    <label>{label}<input className="cloud-in" type={type} autoComplete={auto} spellCheck={false} required placeholder={placeholder}
      value={form[k]} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} data-cloud-field={k} /></label>
  );
  const acct = st.account;

  const p = st.progress, files = p?.files ?? [], pct = p?.bytes_total ? Math.floor((100 * p.bytes_done) / p.bytes_total) : 0;
  const done = st.state === "done";
  // one plain line, only when the person must know something
  const msg = error ?? (live && misses >= 2 ? "This page lost touch with the app. The upload may still be going on." : null) ?? st.exit ?? ({
    running: p?.error ? "No connection to the cloud just now. It carries on by itself." : null,
    paused: "Paused. Resume carries on where it stopped.",
    stopped: "Cut off when the computer or the app stopped. It carries on by itself when the app starts, or now with Resume.",
    failed: `It stopped: ${p?.error || "no reason given"}.`,
  } as Record<string, string | null>)[st.state] ?? null;
  const bad = !!(error || st.exit || (live && misses >= 2) || st.state === "failed");
  return (
    <section className="card box" aria-labelledby="h-upload" data-upload data-ready={ready || undefined} data-upload-state={st.cloud ? st.state : "waiting_for_cloud"}>
      <div className="sec-head">
        <div>
          <h2 id="h-upload">Upload to the cloud</h2>
          <p>Sends this session&rsquo;s recordings and results, unchanged, to FinePoint Cloud.</p>
        </div>
      </div>
      {!st.cloud ? (
        <form className="cloud-form" data-upload-no-cloud data-cloud-signin onSubmit={e => { e.preventDefault(); sign("POST"); }}>
          <p className="note">Sign in to FinePoint Cloud to upload from this computer.</p>
          <div className="cloud-fields">
            {field("url", "Server address", "url", "url", "https://…")}
            {field("email", "Email", "email", "username")}
            {field("password", "Password", "password", "current-password")}
            <button type="submit" className="btn primary" disabled={signing} data-cloud-signin-go>{signing ? "Signing in…" : "Sign in"}</button>
          </div>
          {signError && <p className="run-msg is-err" data-cloud-error>{signError}</p>}
        </form>
      ) : (
        <>
          <div className="cloud-who" data-cloud-signed-in>
            <span>{acct.fromEnv ? "Signed in for this demo" : `Signed in as ${acct.email ?? "?"}${acct.lab ? ` (lab ${acct.lab})` : ""}`}</span>
            {!acct.fromEnv && <button type="button" className="btn secondary sm" onClick={() => sign("DELETE")} disabled={signing} data-cloud-signout>Sign out</button>}
            {signError && <span className="run-msg is-err" data-cloud-error>{signError}</span>}
          </div>
          <div className="run-bar">
            {done && <span className="up-done up-finished" data-upload-msg><Icon name="checkc" />Uploaded. Everything is in the cloud.</span>}
            {live
              ? <button type="button" className="btn secondary" onClick={() => call("DELETE")} disabled={sent || st.state === "pausing"} data-upload-pause><Icon name="pause" />{st.state === "pausing" ? "Pausing…" : "Pause"}</button>
              : <button type="button" className={`btn ${done ? "secondary" : "primary"}`} onClick={() => call("POST")} disabled={sent} data-upload-start><Icon name={done ? "refresh" : "upload"} />{["paused", "stopped"].includes(st.state) ? "Resume" : st.state === "failed" ? "Try again" : done ? "Upload again" : "Upload"}</button>}
          </div>
          {!["none", "done"].includes(st.state) && (
            <div className="up-total" data-upload-total={pct}>
              <div className="up-bar" role="progressbar" aria-label="Uploaded" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><i style={{ width: `${pct}%` }} /></div>
              <span><b>{pct}%</b>{live && (p?.bytes_done ? (p.eta_s != null ? ` · ${timeLeft(p.eta_s)}` : "") : " · Getting ready")}</span>
            </div>
          )}
          {msg && <p className={`run-msg${bad ? " is-err" : ""}`} data-upload-msg>{msg}</p>}
          {files.length > 0 && (
            <details className="fold">
              <summary>Show files</summary>
              <div className="tbl-wrap">
                <table className="files up-files" data-upload-files>
                  {GROUPS.map(kind => {
                    const rows = files.filter(f => (kind === "sync" ? f.kind === "sync" || f.kind === "manifest" : f.kind === kind));
                    return rows.length > 0 && (
                      <tbody key={kind} data-upload-group={kind}>
                        {rows.map(f => (
                          <tr key={f.path} data-upload-file={f.path} data-upload-status={f.status}>
                            <td className="c-file mono">{f.path}</td>
                            <td className={f.status === "failed" ? "run-msg is-err" : undefined}>{f.status === "done" ? <span className="up-done"><Icon name="check" />Done</span> : fileWord(f, live)}</td>
                          </tr>
                        ))}
                      </tbody>
                    );
                  })}
                </table>
              </div>
            </details>
          )}
          {st.cloudError && <p className="note bad-note" data-upload-cloud-error>{st.cloudError}</p>}
          {st.inCloud && (
            <details className="fold" data-in-cloud={st.inCloud.files.length}>
              <summary>What the cloud holds: {num(st.inCloud.files.length)} files{st.inCloud.uploading.length ? `, ${st.inCloud.uploading.length} not finished` : ""}</summary>
              <div className="tbl-wrap">
                <table className="files">
                  <thead><tr><th>File</th><th>Stored (UTC)</th></tr></thead>
                  <tbody>{st.inCloud.files.map(f => (
                    <tr key={f.path} data-cloud-file={f.path}><td className="c-file mono">{f.path}</td><td className="mono">{f.modified.replace("T", " ").replace(/\.\d+|Z$/g, "")}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
