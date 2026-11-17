// SPDX-License-Identifier: GPL-3.0-or-later
// Folder paths typed by the user. The engine needs absolute paths: "~/..." is expanded here with the home folder the
// engine reports in /api/health (the engine also expands "~/" itself, as a safety net).

export function isAbsolutePath(p: string): boolean {
  return p.startsWith('/') || /^[A-Za-z]:[\\/]/.test(p) || p.startsWith('\\\\');
}

/** "~" or "~/x" -> "<home>/x" (only the user's own home; "~other" is left as typed). Unknown home: unchanged. */
export function expandHome(p: string, home: string | null | undefined): string {
  const s = p.trim();
  if (!home || !(s === '~' || s.startsWith('~/'))) return s;
  return home.replace(/\/+$/, '') + s.slice(1);
}

/** Expanded path, or null when it is still not absolute (e.g. "~/x" before the engine said where home is). */
export function absolutePath(p: string, home: string | null | undefined): string | null {
  const e = expandHome(p, home);
  return e && isAbsolutePath(e) ? e : null;
}
