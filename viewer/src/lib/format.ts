// Small display helpers shared by server pages and client components. Pure.
import { shortLabel, type Camera } from "./session";

export const num = (v: number | null | undefined, d = 0) =>
  v == null ? "" : Number(v).toLocaleString("en", { minimumFractionDigits: d, maximumFractionDigits: d });

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${num(n / 1e9, 1)} GB`;
  if (n >= 1e6) return `${num(n / 1e6, 0)} MB`;
  if (n >= 1e3) return `${num(n / 1e3, 0)} kB`;
  return `${num(n)} bytes`;
}

export const fmtMin = (s: number) => `${num(s / 60, 1)} min`;

// "in 4 min 53 s", "in 1.4 s": how long until a camera picks up again.
export function waitText(s: number): string {
  if (s < 10) return `${num(s, 1)} s`;
  const r = Math.round(s), m = Math.floor(r / 60);
  if (r < 60) return `${r} s`;
  return r % 60 ? `${m} min ${r % 60} s` : `${m} min`;
}

// A Windows path: C:\…, or \\server\share\…. Its names split at \ and /, any other path's at / only.
export const windowsPath = (p: string) => /^([A-Za-z]:[\\/]|\\\\)/.test(p);
export const pathParts = (p: string) => p.split(windowsPath(p) ? /[\\/]/ : "/").filter(Boolean);
// The last name in a path: the file's, or the folder's.
export const fileName = (p: string) => pathParts(p).pop() ?? p;
// A file of a session by its absolute path (the server checks it lies in the session or its video folders). A Windows
// path goes by its names too: C:\a\b.mp4 as C%3A/a/b.mp4, \\server\share\b.mp4 as server/share/b.mp4.
export const mediaUrl = (job: string, abs: string) => `/api/media/${encodeURIComponent(job)}/${pathParts(abs).map(encodeURIComponent).join("/")}`;
// One video of a session in the single view: its camera and part, with "depth" for a depth camera's depth video.
export const watchHref = (job: string, cam: string, part: string, query = "", depth = false) =>
  `/jobs/${encodeURIComponent(job)}/watch/${encodeURIComponent(cam)}/${encodeURIComponent(part)}${depth ? "/depth" : ""}${query ? `?${query}` : ""}`;
export const jobHref = (job: string, sub = "") => `/jobs/${encodeURIComponent(job)}${sub ? `/${sub}` : ""}`;
export const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;

// Search parameters as the pages read them.
export const firstParam = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
export const numParam = (v: string | string[] | undefined) => { const x = firstParam(v); return x != null && x !== "" && Number.isFinite(+x) ? +x : null; };
// A page's route params arrive still percent-encoded ("act1%20oct"); route handlers get them decoded.
export const fromParam = (s: string) => { try { return decodeURIComponent(s); } catch { return s; } };

// Where space is short: "Camera 1" and "Depth camera" stay, longer names keep their first word.
export const camName = (cam: Camera) => { const w = shortLabel(cam).split(" "); return w.length > 2 ? w[0] : w.join(" "); };
