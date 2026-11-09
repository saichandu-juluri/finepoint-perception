"use client";
// Links that respect unsaved transcript edits, and the shared "is the user typing" test for keyboard shortcuts.
import Link from "next/link";
import type { ComponentProps } from "react";

let unsaved = false;
// The editor sets this while it has unsaved changes and clears it when it unmounts or is hidden.
export const setUnsaved = (v: boolean) => { unsaved = v; };
export const confirmLeave = () => !unsaved || window.confirm("This transcript has changes that are not saved yet. Leave without saving?");

export function NavLink({ onNavigate, ...props }: ComponentProps<typeof Link>) {
  return <Link {...props} onNavigate={e => (confirmLeave() ? onNavigate?.(e) : e.preventDefault())} />;
}

export function isTyping(el: EventTarget | Element | null): boolean {
  const t = el as HTMLElement | null;
  return !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}
