// Inline SVG icons (stroke 1.8, 24 px grid). Decorative: the control carrying one has its own label.
import type { ReactNode } from "react";

const P: Record<string, ReactNode> = {
  play: <path d="M8 5.8v12.4a.9.9 0 0 0 1.37.77l9.9-6.2a.9.9 0 0 0 0-1.54l-9.9-6.2A.9.9 0 0 0 8 5.8z" fill="currentColor" stroke="none" />,
  pause: <><rect x="6.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none" /><rect x="13.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none" /></>,
  fback: <><path d="M16 6l-6 6 6 6" /><path d="M7 6v12" /></>,
  ffwd: <><path d="M8 6l6 6-6 6" /><path d="M17 6v12" /></>,
  back5: <><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" /><path d="M4 4v4h4" /><path d="M10.5 9.5h3.2M10.5 9.5v2.6c.5-.3 1-.4 1.6-.4a1.8 1.8 0 0 1 0 3.6c-.7 0-1.3-.3-1.7-.8" /></>,
  fwd5: <><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" /><path d="M20 4v4h-4" /><path d="M10.5 9.5h3.2M10.5 9.5v2.6c.5-.3 1-.4 1.6-.4a1.8 1.8 0 0 1 0 3.6c-.7 0-1.3-.3-1.7-.8" /></>,
  vol: <><path d="M4 9.5h3l4.5-4v13L7 14.5H4z" /><path d="M15.5 9a4 4 0 0 1 0 6" /><path d="M18.3 6.5a7.5 7.5 0 0 1 0 11" /></>,
  mute: <><path d="M4 9.5h3l4.5-4v13L7 14.5H4z" /><path d="m16 9.5 5 5M21 9.5l-5 5" /></>,
  full: <><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4.2-4.2" /></>,
  upload: <><path d="M12 15.5V4.5" /><path d="m7.5 9 4.5-4.5L16.5 9" /><path d="M4.5 15v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15" /></>,
  download: <><path d="M12 4.5v11" /><path d="m7.5 11 4.5 4.5 4.5-4.5" /><path d="M4.5 15v2.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15" /></>,
  undo: <><path d="M9 14.5 4.5 10 9 5.5" /><path d="M4.5 10H15a4.5 4.5 0 0 1 0 9h-3" /></>,
  redo: <><path d="M15 14.5 19.5 10 15 5.5" /><path d="M19.5 10H9a4.5 4.5 0 0 0 0 9h3" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  checkc: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 3 3 5-6" /></>,
  cut: <><circle cx="6.5" cy="7" r="2.5" /><circle cx="6.5" cy="17" r="2.5" /><path d="M8.6 8.4 19 17M8.6 15.6 19 7" /></>,
  merge: <><path d="M6 4v4.5A3.5 3.5 0 0 0 9.5 12h5A3.5 3.5 0 0 0 18 8.5V4" /><path d="M12 12v8" /><path d="m9 17 3 3 3-3" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <><path d="M4.5 7h15" /><path d="M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2" /><path d="m6.5 7 .8 11.2a2 2 0 0 0 2 1.8h5.4a2 2 0 0 0 2-1.8L17.5 7" /></>,
  chevL: <path d="m14.5 6-6 6 6 6" />,
  chevR: <path d="m9.5 6 6 6-6 6" />,
  folder: <path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2h7.4a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  video: <><rect x="3" y="6.5" width="12.5" height="11" rx="2.2" /><path d="m15.5 10.5 5-3v9l-5-3" /></>,
  views: <><rect x="2.5" y="6.5" width="5.5" height="11" rx="1.4" /><rect x="9.25" y="6.5" width="5.5" height="11" rx="1.4" /><rect x="16" y="6.5" width="5.5" height="11" rx="1.4" /></>,
  single: <><rect x="3" y="5.5" width="11" height="13" rx="2" /><path d="M17 8h4M17 12h4M17 16h4" /></>,
  library: <><path d="M3.5 5h4v14h-4zM9.5 5h4v14h-4z" /><path d="m15.2 6.2 3.7-.9 2.9 13.3-3.7.9z" /></>,
  alert: <><path d="M10.3 4.9 2.9 17.7A2 2 0 0 0 4.6 20.7h14.8a2 2 0 0 0 1.7-3L13.7 4.9a2 2 0 0 0-3.4 0z" /><path d="M12 9.5v4.5" /><path d="M12 17.2h.01" /></>,
  doc: <><path d="M7 3.5h7l4 4v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" /><path d="M14 3.5V8h4" /><path d="M9 12.5h6M9 16h4" /></>,
  camoff: <><path d="M3 3l18 18" /><path d="M16 16.5V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h1" /><path d="M10.5 6H14a2 2 0 0 1 2 2v3.5l5-3v8" /></>,
  arrowR: <><path d="M5 12h14" /><path d="m13 6 6 6-6 6" /></>,
  arrowL: <><path d="M19 12H5" /><path d="m11 6-6 6 6 6" /></>,
  start: <><path d="M5 5v14" /><path d="M19 12H9" /><path d="m12.5 8.5-3.5 3.5 3.5 3.5" /></>,
  end: <><path d="M19 5v14" /><path d="M5 12h10" /><path d="m11.5 8.5 3.5 3.5-3.5 3.5" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5" /><path d="M12 8h.01" /></>,
  flag: <><path d="M5.5 20.5v-16" /><path d="M5.5 4.5h11l-2 3.5 2 3.5h-11" /></>,
  clock: <><circle cx="12" cy="13" r="7.5" /><path d="M12 9.5V13l2.5 1.5" /><path d="M10 2.5h4" /></>,
  open: <><path d="M14 5h5v5" /><path d="M19 5l-8 8" /><path d="M18 14v4a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 18V8a1.5 1.5 0 0 1 1.5-1.5H10" /></>,
  edit: <><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" /><path d="m13.5 6.5 4 4" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8" /><path d="M4 4v4h4" /><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16" /><path d="M20 20v-4h-4" /></>,
  kbd: <><rect x="2.5" y="6" width="19" height="12" rx="2" /><path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9" /></>,
  more: <><circle cx="5.5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="18.5" cy="12" r="1.2" fill="currentColor" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3.5" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
};

export function Icon({ name, size, className }: { name: keyof typeof P | string; size?: number; className?: string }) {
  return (
    <svg className={className ? `i ${className}` : "i"} viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
      {P[name]}
    </svg>
  );
}

export function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" fill="#0F766E" />
      <path d="M8 11.5v-2A1.5 1.5 0 0 1 9.5 8h2M16.5 8h2A1.5 1.5 0 0 1 20 9.5v2M20 16.5v2a1.5 1.5 0 0 1-1.5 1.5h-2M11.5 20h-2A1.5 1.5 0 0 1 8 18.5v-2" stroke="#fff" strokeWidth="1.9" fill="none" strokeLinecap="round" />
      <circle cx="14" cy="14" r="2.3" fill="#fff" />
    </svg>
  );
}
