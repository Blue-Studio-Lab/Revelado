// SPDX-License-Identifier: GPL-3.0-or-later
// Small hardening helpers for data that arrives from the engine (HTTP / WebSocket):
//  - safeUrl: only same-origin (or configured-engine-origin) http(s) URLs may reach
//    an <img src>, <a href> or window.open — never javascript:, data:, file:, etc.
//  - safe keyed records: ids from the engine are used as object keys, so reject
//    prototype-polluting names and keep the records prototype-less.

const ALLOWED_PROTOCOLS = new Set(['http:', 'https:']);

/**
 * Resolves `raw` (an absolute URL or a path relative to `base`) and returns its
 * href only when it is http(s) and its origin is one of `allowedOrigins`.
 * Returns undefined for anything else (other schemes, foreign hosts, garbage).
 */
export function safeUrl(raw: string | null | undefined, base: string, allowedOrigins: readonly string[]): string | undefined {
  if (typeof raw !== 'string') return undefined;
  const s = raw.trim();
  if (!s) return undefined;
  // reject control characters outright (browsers strip some of them before parsing the scheme)
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(s)) return undefined;
  let u: URL;
  try {
    u = new URL(s, base);
  } catch {
    return undefined;
  }
  if (!ALLOWED_PROTOCOLS.has(u.protocol)) return undefined;
  if (u.username || u.password) return undefined;
  // Rebuild the result from the trusted origin constant rather than returning
  // u.href, so the scheme + host always come from `allowedOrigins` (for an http(s)
  // URL without credentials this is exactly u.href).
  const origin = allowedOrigins.find((o) => o === u.origin);
  if (origin === undefined) return undefined;
  return origin + u.pathname + u.search + u.hash;
}

const UNSAFE_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

/** True for a key that is safe to use as an own property name of a lookup record. */
export function isSafeKey(k: unknown): k is string {
  return typeof k === 'string' && k.length > 0 && k.length <= 512 && !UNSAFE_KEYS.has(k);
}

/** An empty prototype-less record (no inherited `constructor`, `toString`, …). */
export function emptyRecord<V>(): Record<string, V> {
  return Object.create(null) as Record<string, V>;
}

/**
 * Returns a copy of `rec` (prototype-less) with `key` set to `value`.
 * Unsafe keys (`__proto__`, `constructor`, `prototype`, non-strings, empty) leave
 * the record unchanged — the same object is returned so React skips the update.
 */
export function withEntry<V>(rec: Record<string, V>, key: unknown, value: V): Record<string, V> {
  if (!isSafeKey(key)) return rec;
  const out = emptyRecord<V>();
  for (const k of Object.keys(rec)) {
    if (isSafeKey(k)) Object.defineProperty(out, k, { value: rec[k], enumerable: true, writable: true, configurable: true });
  }
  // explicit guard at the write site (prototype-polluting names never reach the null-prototype record)
  if (key === '__proto__' || key === 'constructor' || key === 'prototype') return rec;
  Object.defineProperty(out, key, { value, enumerable: true, writable: true, configurable: true });
  return out;
}

/** Own-property lookup that never reaches the prototype chain. */
export function getEntry<V>(rec: Record<string, V>, key: unknown): V | undefined {
  return isSafeKey(key) && Object.prototype.hasOwnProperty.call(rec, key) ? rec[key] : undefined;
}
