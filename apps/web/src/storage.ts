// SPDX-License-Identifier: GPL-3.0-or-later
// Browser storage keys. Only per-viewer conveniences live here (language,
// recent folders, per-folder UI state). Never API keys, tokens or photos.
//
// Earlier builds stored keys under other prefixes (LEGACY_STORAGE_PREFIXES in
// brand.ts). migrateLegacyKeys() moves them once.
import { LEGACY_STORAGE_PREFIXES, STORAGE_PREFIX } from './brand';

export const PREFIX = STORAGE_PREFIX;
export const LEGACY_PREFIXES = LEGACY_STORAGE_PREFIXES;
export const PREFS_KEY = `${PREFIX}prefs.v1`;
export const EXPORT_DEST_KEY = `${PREFIX}exportDest`;
export const EXPORT_OPTS_KEY = `${PREFIX}exportOpts`;
export const sessionKey = (sid: string) => `${PREFIX}session.${sid}`;

export type KV = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;

function store(): KV | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null; // blocked storage (private window, sandboxed iframe)
  }
}

function keysOf(s: KV): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i++) {
    const k = s.key(i);
    if (k !== null) out.push(k);
  }
  return out;
}

/** Copies every legacy-prefixed key to the current prefix (never overwriting a
 *  value that already exists there) and removes the old key. Idempotent; safe
 *  to call on every start. Returns the number of keys copied. */
export function migrateLegacyKeys(s: KV | null = store(), legacy: string[] = LEGACY_PREFIXES, prefix: string = PREFIX): number {
  if (!s) return 0;
  let moved = 0;
  try {
    for (const k of keysOf(s)) {
      const old = legacy.find((p) => p !== prefix && k.startsWith(p));
      if (!old) continue;
      const next = prefix + k.slice(old.length);
      const v = s.getItem(k);
      if (v !== null && s.getItem(next) === null) {
        s.setItem(next, v);
        moved++;
      }
      s.removeItem(k);
    }
  } catch {
    /* quota or access error: keep whatever is there */
  }
  return moved;
}

export function readItem(key: string): string | null {
  try {
    return store()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function writeItem(key: string, value: string): void {
  try {
    store()?.setItem(key, value);
  } catch {
    /* storage unavailable: the value just won't persist */
  }
}

/** Removes every key this app owns (current and legacy prefixes). */
export function clearAppData(s: KV | null = store()): void {
  if (!s) return;
  try {
    for (const k of keysOf(s)) if (k.startsWith(PREFIX) || LEGACY_PREFIXES.some((p) => k.startsWith(p))) s.removeItem(k);
  } catch {
    /* ignore */
  }
}
