// SPDX-License-Identifier: GPL-3.0-or-later
// What the running engine can do, read defensively from GET /api/health and
// from each photo. The UI never assumes a feature exists just because a newer
// engine might have it.
//
// Health (all optional):
//   capabilities.develop_jpg     bool  the engine CAN develop JPG-only photos, but only when the recipe asks for
//   capabilities.jpg_only_ok     bool  develop_source: "jpg" (same meaning; either is enough). The UI does not
//                                      offer that mode yet, so JPG-only photos still need the RAW here.
//   capabilities.raw_only_ok     bool  false → RAW-only photos cannot be developed
//   capabilities.tiff16_rotation bool  false → TIFF16 exports are not straightened
// Photo (all optional except has_raw):
//   has_raw, has_jpg, can_develop, develop_block_reason (code or message)
// Fallback when the engine sends none of the above: has_raw=false ⇒ cannot develop.
import type { Health, Photo } from './api';

export interface EngineCaps {
  /** The engine can develop JPG-only photos from the camera JPEG (opt-in develop_source: "jpg", not offered yet). */
  developJpg: boolean;
  /** RAW-only photos can be developed (true unless the engine says otherwise). */
  developRawOnly: boolean;
  /** Show the note that TIFF16 exports are not straightened. */
  tiffRotationNote: boolean;
}

export const NO_CAPS: EngineCaps = { developJpg: false, developRawOnly: true, tiffRotationNote: false };

type Dict = Record<string, unknown>;
const obj = (v: unknown): Dict | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : null);

export function engineCaps(health: Health | null | undefined): EngineCaps {
  if (!health) return NO_CAPS;
  const c = obj(health.capabilities) ?? {};
  return {
    developJpg: c.develop_jpg === true || c.jpg_only_ok === true,
    developRawOnly: c.raw_only_ok !== false,
    tiffRotationNote: c.tiff16_rotation === false,
  };
}

export type PairKind = 'both' | 'raw' | 'jpg';

export function hasJpg(p: Pick<Photo, 'jpg_path'> & { has_jpg?: unknown }): boolean {
  return typeof p.has_jpg === 'boolean' ? p.has_jpg : !!p.jpg_path;
}

export function pairOf(p: Pick<Photo, 'has_raw' | 'jpg_path'> & { has_jpg?: unknown }): PairKind {
  if (p.has_raw) return hasJpg(p) ? 'both' : 'raw';
  return 'jpg';
}

/** Engine codes meaning "this photo has no RAW". Other reasons are shown as the engine sent them. */
const NEEDS_RAW = new Set(['needs_raw', 'no_raw', 'missing_raw', 'jpg_only', 'raw_required']);

export interface Developability {
  ok: boolean;
  /** 'needs_raw', 'blocked', or the engine's own reason; null when ok. */
  reason: string | null;
}

export function developability(p: Photo, caps: EngineCaps): Developability {
  const r = p.develop_block_reason;
  const engineReason = typeof r === 'string' && r.trim() ? r.trim() : null;
  if (typeof p.can_develop === 'boolean') {
    if (p.can_develop) return { ok: true, reason: null };
    return { ok: false, reason: engineReason ?? (p.has_raw ? 'blocked' : 'needs_raw') };
  }
  const kind = pairOf(p);
  // JPG-only: the engine refuses without develop_source "jpg" (422 E_NEEDS_RAW), which this UI never sends.
  if (kind === 'jpg') return { ok: false, reason: 'needs_raw' };
  if (kind === 'raw' && !caps.developRawOnly) return { ok: false, reason: engineReason ?? 'blocked' };
  return { ok: true, reason: null };
}

export const canDevelop = (p: Photo, caps: EngineCaps): boolean => developability(p, caps).ok;

/** Human text for a block reason. `t` is the i18n lookup. */
export function reasonText(reason: string | null, t: (k: 'dev.needsRaw' | 'dev.blocked') => string): string {
  if (!reason) return '';
  const r = reason.toLowerCase();
  if (NEEDS_RAW.has(r)) return t('dev.needsRaw');
  if (r === 'blocked') return t('dev.blocked');
  return reason;
}
