// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import type { Health, Photo } from './api';
import { APP_NAME, LEGACY_STORAGE_PREFIXES, STORAGE_PREFIX } from './brand';
import { canDevelop, developability, engineCaps, NO_CAPS, pairOf, reasonText } from './capabilities';
import type { RecipePreset } from './api';
import { absolutePath, expandHome, isAbsolutePath } from './paths';
import {
  batchOverrides,
  cleanRecipe,
  cliCommand,
  defaultsOf,
  diffRecipe,
  effectiveRecipe,
  FALLBACK_PRESETS,
  FALLBACK_SCHEMA,
  fromYaml,
  matchPreset,
  normalisePresets,
  presetLabel,
  toYaml,
  visibleSchema,
  withOverride,
} from './recipe';
import { clearAppData, migrateLegacyKeys, type KV } from './storage';

function mem(init: Record<string, string> = {}): KV & { data: Map<string, string> } {
  const data = new Map(Object.entries(init));
  return {
    data,
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
  };
}

describe('brand', () => {
  it('is Revelado and migrates both earlier prefixes', () => {
    expect(APP_NAME).toBe('Revelado');
    expect(STORAGE_PREFIX).toBe('revelado.');
    expect(LEGACY_STORAGE_PREFIXES).toEqual(['darkroom.', 'cameraman.']);
  });
});

describe('storage migration', () => {
  it('moves legacy keys, keeps newer values, is idempotent', () => {
    const s = mem({
      'darkroom.prefs.v1': '{"lang":"en"}',
      'cameraman.session.a': 'old',
      'revelado.session.a': 'new',
      'other.app': 'x',
    });
    expect(migrateLegacyKeys(s)).toBe(1);
    expect(s.getItem('revelado.prefs.v1')).toBe('{"lang":"en"}');
    expect(s.getItem('revelado.session.a')).toBe('new');
    expect(s.getItem('darkroom.prefs.v1')).toBeNull();
    expect(s.getItem('cameraman.session.a')).toBeNull();
    expect(s.getItem('other.app')).toBe('x');
    expect(migrateLegacyKeys(s)).toBe(0);
  });

  it('clearAppData removes only this app', () => {
    const s = mem({ 'revelado.a': '1', 'darkroom.b': '2', 'other.c': '3' });
    clearAppData(s);
    expect([...s.data.keys()]).toEqual(['other.c']);
  });

  it('tolerates missing storage', () => {
    expect(migrateLegacyKeys(null)).toBe(0);
  });
});

describe('capabilities', () => {
  const photo = (p: Partial<Photo>) => ({ id: 'p', has_raw: false, jpg_path: 'a.jpg', ...p }) as Photo;

  const h = (capabilities: Record<string, unknown>) => ({ capabilities }) as unknown as Health;

  it('defaults to no JPG develop and no TIFF note', () => {
    expect(engineCaps(null)).toEqual(NO_CAPS);
    expect(engineCaps({ develop_engine: { available: true } } as unknown as Health)).toEqual(NO_CAPS);
  });

  it('reads the new capability flags', () => {
    expect(engineCaps(h({ develop_jpg: true })).developJpg).toBe(true);
    expect(engineCaps(h({ jpg_only_ok: true })).developJpg).toBe(true);
    expect(engineCaps(h({ raw_only_ok: false })).developRawOnly).toBe(false);
    expect(engineCaps(h({ tiff16_rotation: false })).tiffRotationNote).toBe(true);
    expect(engineCaps(h({ tiff16_rotation: true })).tiffRotationNote).toBe(false);
    expect(engineCaps(h({})).tiffRotationNote).toBe(false);
  });

  it('JPG-only photos need the RAW even when the engine could develop JPG (develop_source "jpg" is not offered)', () => {
    expect(developability(photo({}), NO_CAPS)).toEqual({ ok: false, reason: 'needs_raw' });
    expect(developability(photo({}), { ...NO_CAPS, developJpg: true })).toEqual({ ok: false, reason: 'needs_raw' });
    expect(canDevelop(photo({ has_jpg: true, can_develop: false, develop_block_reason: 'needs_raw' }), { ...NO_CAPS, developJpg: true })).toBe(false);
    expect(canDevelop(photo({ has_raw: true }), NO_CAPS)).toBe(true);
    const rawOnly = photo({ has_raw: true, jpg_path: null as unknown as string });
    expect(canDevelop(rawOnly, NO_CAPS)).toBe(true);
    expect(canDevelop(rawOnly, { ...NO_CAPS, developRawOnly: false })).toBe(false);
  });

  it('per-photo engine fields win', () => {
    expect(developability(photo({ has_raw: true, can_develop: false, develop_block_reason: 'corrupt' }), NO_CAPS)).toEqual({ ok: false, reason: 'corrupt' });
    expect(developability(photo({ can_develop: false }), NO_CAPS).reason).toBe('needs_raw');
    expect(canDevelop(photo({ can_develop: true }), NO_CAPS)).toBe(true);
  });

  it('turns reasons into text', () => {
    const t = (k: string) => k;
    expect(reasonText('needs_raw', t)).toBe('dev.needsRaw');
    expect(reasonText('NO_RAW', t)).toBe('dev.needsRaw');
    expect(reasonText('blocked', t)).toBe('dev.blocked');
    expect(reasonText('Archivo dañado', t)).toBe('Archivo dañado');
    expect(reasonText(null, t)).toBe('');
  });

  it('pairs RAW and JPG', () => {
    expect(pairOf({ has_raw: true, jpg_path: 'x.jpg' })).toBe('both');
    expect(pairOf({ has_raw: true, jpg_path: null as unknown as string })).toBe('raw');
    expect(pairOf({ has_raw: false, jpg_path: 'x.jpg' })).toBe('jpg');
  });
});

describe('recipe YAML', () => {
  it('round-trips the defaults', () => {
    const r = defaultsOf(FALLBACK_SCHEMA);
    const back = fromYaml(toYaml(r, FALLBACK_SCHEMA, 'es'), FALLBACK_SCHEMA, 'es');
    expect('recipe' in back && back.recipe).toEqual(r);
  });

  it('never shows develop_source, and rejects it when typed', () => {
    const r = { ...defaultsOf(FALLBACK_SCHEMA), develop_source: 'raw' };
    expect(toYaml(r, FALLBACK_SCHEMA, 'es')).not.toContain('develop_source');
    const engineSchema = { ...FALLBACK_SCHEMA, develop_source: { type: 'enum', options: ['raw', 'jpg'], default: 'raw' } };
    const sc = visibleSchema(engineSchema);
    expect(sc.develop_source).toBeUndefined();
    const res = fromYaml('develop_source: jpg\n', sc, 'es');
    expect('error' in res).toBe(true);
  });
});

describe('recipe defaults and presets', () => {
  it('defaults to neutral with NR/CA live', () => {
    const d = defaultsOf(FALLBACK_SCHEMA);
    expect(d.film).toBe('none');
    expect(d.grain).toBe(0);
    expect(d.skin_retouch).toBe(0);
    expect(d.reframe).toBe('off');
    expect(FALLBACK_SCHEMA.nr.supported).toBe(true);
    expect(FALLBACK_SCHEMA.ca.supported).toBe(true);
    expect(toYaml(d, FALLBACK_SCHEMA, 'es')).not.toContain('ignora');
  });

  it('cleanRecipe drops develop_source and internal keys', () => {
    expect(cleanRecipe({ film: 'none', develop_source: 'jpg', _extra: 'x' })).toEqual({ film: 'none' });
    expect(cleanRecipe(null)).toEqual({});
  });

  it('puts neutral first, labels presets and matches the current recipe', () => {
    const engine: RecipePreset[] = [
      { id: 'editorial_ektar', label_es: 'Editorial · Kodak Ektar 100', recipe: { ...FALLBACK_PRESETS[1].recipe, develop_source: 'raw' } },
      { id: 'neutral', label_es: 'Neutro', recipe: { ...FALLBACK_PRESETS[0].recipe, develop_source: 'raw' }, default: true },
    ];
    const list = normalisePresets(engine);
    expect(list.map((p) => p.id)).toEqual(['neutral', 'editorial_ektar']);
    expect(list[0].recipe.develop_source).toBeUndefined();
    expect(presetLabel(list[0], 'es')).toBe('Neutro');
    expect(presetLabel(list[1], 'es')).toBe('Editorial Ektar');
    expect(presetLabel({ id: 'x', label_es: 'Otro', recipe: {} }, 'es')).toBe('Otro');
    expect(matchPreset(defaultsOf(FALLBACK_SCHEMA), list)?.id).toBe('neutral');
    expect(matchPreset({ ...defaultsOf(FALLBACK_SCHEMA), film: 'kodak_ektar_100', grain: 1, skin_retouch: 60, blemishes: 50, accent: 40, reframe: 'suggest' }, list)?.id).toBe('editorial_ektar');
    expect(matchPreset({ ...defaultsOf(FALLBACK_SCHEMA), grain: 0.3 }, list)).toBeNull();
    expect(normalisePresets(null).map((p) => p.id)).toEqual(['neutral', 'editorial_ektar']);
  });
});

describe('per-photo overrides', () => {
  const base = defaultsOf(FALLBACK_SCHEMA);

  it('stores only what differs from the batch recipe', () => {
    expect(diffRecipe({ ...base, skin_retouch: 60, nr: 'strong' }, base)).toEqual({ skin_retouch: 60, nr: 'strong' });
    expect(diffRecipe({ ...base, develop_source: 'jpg' }, base)).toEqual({});
  });

  it('sets, merges and clears an override', () => {
    let ov = withOverride({}, 'a', { skin_retouch: 60 });
    expect(ov).toEqual({ a: { skin_retouch: 60 } });
    expect(effectiveRecipe(base, ov, 'a').skin_retouch).toBe(60);
    expect(effectiveRecipe(base, ov, 'b')).toBe(base);
    ov = withOverride(ov, 'a', {});
    expect(ov).toEqual({});
  });

  it('sends only non-empty overrides of photos in the batch', () => {
    const ov = { a: { skin_retouch: 60 }, b: {}, gone: { grain: 1 } };
    expect(batchOverrides(ov, ['a', 'b', 'c'])).toEqual({ a: { skin_retouch: 60 } });
    expect(batchOverrides(undefined, ['a'])).toEqual({});
  });

  it('includes overrides in the copied batch command, never the token', () => {
    const cmd = cliCommand(base, { engineUrl: 'http://127.0.0.1:8790/', photoIds: ['a', 'b'], batch: true, overrides: { a: { skin_retouch: 60 }, x: { grain: 1 } } });
    expect(cmd).toContain('http://127.0.0.1:8790/api/develop/batch');
    expect(cmd).toContain('"overrides":{"a":{"skin_retouch":60}}');
    expect(cmd).not.toContain('"x"');
    expect(cmd).toContain('$REVELADO_TOKEN');
    const plain = cliCommand(base, { engineUrl: 'http://e', photoIds: ['a'], batch: true });
    expect(plain).not.toContain('overrides');
  });
});

describe('paths', () => {
  it('expands only the user\'s own "~/"', () => {
    expect(expandHome('~/revelado-demo/raws_Entrega', '/Users/me')).toBe('/Users/me/revelado-demo/raws_Entrega');
    expect(expandHome('~', '/Users/me/')).toBe('/Users/me');
    expect(expandHome('~other/x', '/Users/me')).toBe('~other/x');
    expect(expandHome('~/x', null)).toBe('~/x');
  });
  it('absolutePath never returns a "~" path', () => {
    expect(absolutePath(' ~/x ', '/Users/me')).toBe('/Users/me/x');
    expect(absolutePath('~/x', undefined)).toBeNull();
    expect(absolutePath('Entrega', '/Users/me')).toBeNull();
    expect(absolutePath('C:\\Fotos', null)).toBe('C:\\Fotos');
    expect(isAbsolutePath('/a')).toBe(true);
  });
});
