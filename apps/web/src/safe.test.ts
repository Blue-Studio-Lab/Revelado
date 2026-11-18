// SPDX-License-Identifier: GPL-3.0-or-later
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setApiBase } from './api';
import { emptyRecord, getEntry, isSafeKey, safeUrl, withEntry } from './safe';

const ORIGIN = 'http://localhost:5173';

describe('safeUrl', () => {
  const ok = (u: string) => safeUrl(u, ORIGIN + '/', [ORIGIN]);

  it('accepts relative engine paths and same-origin http(s) URLs', () => {
    expect(ok('/api/outputs/job1/a.jpg')).toBe(ORIGIN + '/api/outputs/job1/a.jpg');
    expect(ok('api/outputs/a.jpg')).toBe(ORIGIN + '/api/outputs/a.jpg');
    expect(ok(ORIGIN + '/api/outputs/a%20b.jpg?x=1')).toBe(ORIGIN + '/api/outputs/a%20b.jpg?x=1');
  });

  it('rejects dangerous schemes', () => {
    for (const bad of [
      'javascript:alert(1)',
      ' JavaScript:alert(1)',
      'java\tscript:alert(1)',
      'data:text/html,<script>alert(1)</script>',
      'vbscript:msgbox(1)',
      'file:///etc/passwd',
      'blob:http://localhost:5173/abc',
    ]) {
      expect(ok(bad), bad).toBeUndefined();
    }
  });

  it('rejects foreign origins, protocol-relative URLs and credentials', () => {
    expect(ok('https://evil.example/x.jpg')).toBeUndefined();
    expect(ok('//evil.example/x.jpg')).toBeUndefined();
    expect(ok('http://localhost:5174/x.jpg')).toBeUndefined();
    expect(ok('http://user:pw@localhost:5173/x.jpg')).toBeUndefined();
  });

  it('builds the result from the trusted origin and matches the parsed href', () => {
    const origins = [ORIGIN, 'http://127.0.0.1:8765'];
    for (const raw of [
      '/api/outputs/j/a.jpg',
      '/api/outputs/j/a b.jpg?size=1024#top',
      'HTTP://LOCALHOST:5173/api/x.jpg',
      'http://127.0.0.1:8765/api/outputs/j/b.jpg',
      '/api/../api/outputs/./c.jpg',
    ]) {
      const out = safeUrl(raw, ORIGIN + '/', origins);
      expect(out, raw).toBe(new URL(raw, ORIGIN + '/').href);
      expect(origins.some((o) => out!.startsWith(o + '/')), raw).toBe(true);
    }
  });

  it('rejects empty and non-string input', () => {
    expect(ok('')).toBeUndefined();
    expect(ok('   ')).toBeUndefined();
    expect(safeUrl(null, ORIGIN, [ORIGIN])).toBeUndefined();
    expect(safeUrl(undefined, ORIGIN, [ORIGIN])).toBeUndefined();
  });
});

describe('api.safeAssetUrl', () => {
  afterEach(() => {
    setApiBase('');
    vi.unstubAllGlobals();
  });

  it('keeps engine outputs working through the same-origin proxy', () => {
    vi.stubGlobal('location', new URL(ORIGIN + '/#/oven'));
    expect(api.safeAssetUrl('/api/outputs/j/a.jpg')).toBe(ORIGIN + '/api/outputs/j/a.jpg');
    expect(api.safeAssetUrl('outputs/j/a.jpg')).toBe(ORIGIN + '/api/outputs/j/a.jpg');
    expect(api.safeAssetUrl(null)).toBeUndefined();
    expect(api.safeAssetUrl('javascript:alert(1)')).toBeUndefined();
    expect(api.safeAssetUrl('data:image/png;base64,AAAA')).toBeUndefined();
    expect(api.safeAssetUrl('https://evil.example/a.jpg')).toBeUndefined();
  });

  it('allows the configured engine origin (Settings → Engine override)', () => {
    vi.stubGlobal('location', new URL(ORIGIN + '/'));
    setApiBase('http://127.0.0.1:8765');
    expect(api.safeAssetUrl('/api/outputs/j/a.jpg')).toBe('http://127.0.0.1:8765/api/outputs/j/a.jpg');
    expect(api.safeAssetUrl('http://127.0.0.1:8765/api/outputs/j/b.jpg')).toBe('http://127.0.0.1:8765/api/outputs/j/b.jpg');
    expect(api.safeAssetUrl('http://127.0.0.1:9999/a.jpg')).toBeUndefined();
  });

  it('accepts an engine override typed with an upper-case scheme', () => {
    vi.stubGlobal('location', new URL(ORIGIN + '/'));
    setApiBase('HTTP://127.0.0.1:8765');
    expect(api.safeAssetUrl('/api/outputs/j/a.jpg')).toBe('http://127.0.0.1:8765/api/outputs/j/a.jpg');
    expect(api.eventsUrl()).toBe('ws://127.0.0.1:8765/api/events');
  });
});

describe('safe keyed records', () => {
  it('rejects prototype-polluting and malformed keys', () => {
    for (const k of ['__proto__', 'constructor', 'prototype', '', 42, null, undefined, {}]) expect(isSafeKey(k)).toBe(false);
    expect(isSafeKey('job-123')).toBe(true);
  });

  it('withEntry copies into a prototype-less record and ignores unsafe keys', () => {
    const a = withEntry(emptyRecord<number>(), 'j1', 1);
    const b = withEntry(a, 'j2', 2);
    expect(Object.getPrototypeOf(b)).toBeNull();
    expect({ ...b }).toEqual({ j1: 1, j2: 2 });
    expect(a).not.toBe(b);
    expect({ ...a }).toEqual({ j1: 1 });

    for (const k of ['__proto__', 'constructor', 'prototype']) {
      expect(withEntry(b, k, 99)).toBe(b);
    }
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(Object.prototype.hasOwnProperty.call(Object.prototype, 'j1')).toBe(false);
  });

  it('getEntry only returns own properties', () => {
    const rec = withEntry(emptyRecord<string>(), 'j1', 'x');
    expect(getEntry(rec, 'j1')).toBe('x');
    expect(getEntry(rec, 'constructor')).toBeUndefined();
    expect(getEntry(rec, 'toString')).toBeUndefined();
    expect(getEntry({ a: 1 } as Record<string, number>, 'hasOwnProperty')).toBeUndefined();
    expect('j1' in rec).toBe(true);
    expect('constructor' in rec).toBe(false);
  });
});
