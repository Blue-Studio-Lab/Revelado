// SPDX-License-Identifier: GPL-3.0-or-later
// Recipe helpers: control metadata (fallback when /api/recipe/schema is not
// available), a tiny flat-YAML writer/parser for the side panel, and the CLI line.
import type { Recipe, RecipeFieldSchema, RecipePreset, RecipeSchema, RecipeValue } from './api';

// Mirrors the engine's schema: the default recipe is NEUTRAL (film "none", no grain, no retouch, no reframing).
export const FALLBACK_SCHEMA: RecipeSchema = {
  film: { type: 'string', default: 'none', label_es: 'Película', label_en: 'Film stock', supported: true },
  film_strength: { type: 'number', min: 0, max: 1, step: 0.05, default: 1, label_es: 'Intensidad', label_en: 'Strength', supported: true },
  exposure_ev: { type: 'number', min: -2, max: 2, step: 0.1, default: 0, label_es: 'Exposición', label_en: 'Exposure', supported: true },
  grain: { type: 'number', min: 0, max: 1.5, step: 0.05, default: 0, label_es: 'Grano', label_en: 'Grain', supported: true },
  skin_retouch: { type: 'number', min: 0, max: 100, step: 1, default: 0, label_es: 'Retoque de piel', label_en: 'Skin retouch', supported: true },
  blemishes: { type: 'number', min: 0, max: 100, step: 1, default: 0, label_es: 'Granitos', label_en: 'Blemishes', supported: true },
  hair: { type: 'enum', options: ['off', 'suave', 'medio', 'fuerte'], default: 'off', label_es: 'Cabello', label_en: 'Hair clean-up', supported: true },
  accent: { type: 'number', min: 0, max: 100, step: 1, default: 0, label_es: 'Acento sujeto', label_en: 'Subject accent', supported: true },
  reframe: { type: 'enum', options: ['off', 'suggest', 'apply'], default: 'off', label_es: 'Encuadre', label_en: 'Reframing', supported: true },
  sharpen: { type: 'number', min: 0, max: 2, step: 0.05, default: 1, label_es: 'Nitidez', label_en: 'Sharpening', supported: true },
  nr: { type: 'enum', options: ['auto', 'soft', 'strong', 'off'], default: 'auto', label_es: 'Ruido', label_en: 'Noise reduction', supported: true },
  ca: { type: 'enum', options: ['auto', 'off'], default: 'auto', label_es: 'Aberración', label_en: 'Chromatic aberration', supported: true },
};

/** Recipe keys the UI never shows nor sends. `develop_source: "jpg"` (develop a JPG-only photo from the camera
 *  JPEG) exists in the engine but is not offered yet: JPG-only photos keep the "needs the RAW" block. */
export const HIDDEN_FIELDS = ['develop_source'];

const isHidden = (k: string) => HIDDEN_FIELDS.includes(k) || k.startsWith('_');

/** Drops hidden/internal keys (develop_source, _extra…) from a recipe. */
export function cleanRecipe(r: Recipe | null | undefined): Recipe {
  const out: Recipe = {};
  for (const [k, v] of Object.entries(r ?? {})) if (!isHidden(k)) out[k] = v;
  return out;
}

/** Drops hidden fields from the schema the controls and the YAML editor use. */
export function visibleSchema(schema: RecipeSchema): RecipeSchema {
  const out: RecipeSchema = {};
  for (const [k, f] of Object.entries(schema)) if (!isHidden(k)) out[k] = f;
  return out;
}

// ── presets (GET /api/recipe/presets; this list is the fallback)
export const FALLBACK_PRESETS: RecipePreset[] = [
  { id: 'neutral', label_es: 'Neutro', label_en: 'Neutral', recipe: defaultsOf(FALLBACK_SCHEMA), default: true },
  {
    id: 'editorial_ektar',
    label_es: 'Editorial Ektar',
    label_en: 'Editorial Ektar',
    recipe: { ...defaultsOf(FALLBACK_SCHEMA), film: 'kodak_ektar_100', film_strength: 1, grain: 1, skin_retouch: 60, blemishes: 50, accent: 40, reframe: 'suggest', sharpen: 1 },
  },
];

const PRESET_NAMES: Record<string, [string, string]> = {
  neutral: ['Neutro', 'Neutral'],
  editorial_ektar: ['Editorial Ektar', 'Editorial Ektar'],
};

export function presetLabel(p: RecipePreset, lang: 'es' | 'en'): string {
  const known = PRESET_NAMES[p.id];
  if (known) return lang === 'es' ? known[0] : known[1];
  return (lang === 'es' ? p.label_es : p.label_en) || p.label_es || p.id;
}

/** Puts the default preset (neutral) first and drops hidden keys from every preset recipe. */
export function normalisePresets(list: RecipePreset[] | null | undefined): RecipePreset[] {
  const src = Array.isArray(list) && list.length ? list : FALLBACK_PRESETS;
  const out = src.filter((p) => p && typeof p.id === 'string' && p.recipe && typeof p.recipe === 'object').map((p) => ({ ...p, recipe: cleanRecipe(p.recipe) }));
  out.sort((a, b) => Number(!!b.default || b.id === 'neutral') - Number(!!a.default || a.id === 'neutral'));
  return out;
}

const same = (a: RecipeValue | undefined, b: RecipeValue | undefined) => (a ?? null) === (b ?? null);

/** The preset whose recipe equals `r` on every key the preset defines, or null ("custom"). */
export function matchPreset(r: Recipe, presets: RecipePreset[]): RecipePreset | null {
  return presets.find((p) => Object.entries(p.recipe).every(([k, v]) => same(r[k], v))) ?? null;
}

// ── per-photo overrides ("Solo esta foto")
export type Overrides = Record<string, Recipe>;

/** Keys of `next` whose value differs from `base` (a partial recipe; empty = no override). */
export function diffRecipe(next: Recipe, base: Recipe): Recipe {
  const out: Recipe = {};
  for (const [k, v] of Object.entries(next)) if (!isHidden(k) && !same(v, base[k])) out[k] = v;
  return out;
}

/** The recipe a photo is developed with: the batch recipe plus that photo's override. */
export function effectiveRecipe(base: Recipe, overrides: Overrides | null | undefined, photoId: string | null | undefined): Recipe {
  const ov = photoId && overrides ? overrides[photoId] : undefined;
  return ov ? { ...base, ...cleanRecipe(ov) } : base;
}

/** Sets (or clears, when it has no differences left) the override of one photo. */
export function withOverride(overrides: Overrides | null | undefined, photoId: string, partial: Recipe): Overrides {
  const next: Overrides = { ...(overrides ?? {}) };
  const clean = cleanRecipe(partial);
  if (Object.keys(clean).length) next[photoId] = clean;
  else delete next[photoId];
  return next;
}

/** The overrides to send with a batch: only photos in the batch, only non-empty ones. */
export function batchOverrides(overrides: Overrides | null | undefined, photoIds: string[]): Overrides {
  const out: Overrides = {};
  for (const id of photoIds) {
    const ov = overrides?.[id] ? cleanRecipe(overrides[id]) : null;
    if (ov && Object.keys(ov).length) out[id] = ov;
  }
  return out;
}

export const GROUPS: { id: 'exposure' | 'film' | 'portrait' | 'framing' | 'detail'; fields: string[] }[] = [
  { id: 'exposure', fields: ['exposure_ev'] },
  { id: 'film', fields: ['film_strength', 'grain'] },
  { id: 'portrait', fields: ['skin_retouch', 'blemishes', 'hair', 'accent'] },
  { id: 'framing', fields: ['reframe'] },
  { id: 'detail', fields: ['sharpen', 'nr', 'ca'] },
];

export const ORDER = ['film', 'film_strength', 'exposure_ev', 'grain', 'skin_retouch', 'blemishes', 'hair', 'accent', 'reframe', 'sharpen', 'nr', 'ca'];

export function defaultsOf(schema: RecipeSchema): Recipe {
  const r: Recipe = {};
  for (const [k, f] of Object.entries(schema)) r[k] = (f.default ?? null) as RecipeValue;
  return r;
}

export function isNumeric(f: RecipeFieldSchema | undefined): boolean {
  return !!f && (f.type === 'number' || f.type === 'int' || f.type === 'float' || (f.min !== undefined && f.max !== undefined && !f.options));
}

function scalar(v: RecipeValue): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (/^[A-Za-z_][\w.-]*$/.test(v) && !['true', 'false', 'null', 'yes', 'no', 'on', 'off'].includes(v.toLowerCase())) return v;
  return JSON.stringify(v);
}

export function toYaml(r: Recipe, schema: RecipeSchema, lang: 'es' | 'en'): string {
  const keys = [...ORDER.filter((k) => k in r), ...Object.keys(r).filter((k) => !ORDER.includes(k)).sort()];
  const lines = ['# revelado-recipe: 1  (v0.1, plana)'];
  for (const k of keys) {
    const f = schema[k];
    let comment = '';
    if (f && isNumeric(f) && f.min !== undefined && f.max !== undefined) comment = `  # ${f.min}…${f.max}`;
    else if (f?.options) comment = `  # ${f.options.join(' | ')}`;
    if (f && f.supported === false) comment += lang === 'es' ? ' · el motor actual lo ignora' : ' · ignored by the current engine';
    if (!f && isHidden(k)) continue;
    lines.push(`${k}: ${scalar(r[k])}${comment}`);
  }
  return lines.join('\n') + '\n';
}

export interface YamlError {
  line: number;
  message: string;
}

function parseScalar(raw: string): RecipeValue {
  const s = raw.trim();
  if (s === '' || s === 'null' || s === '~') return null;
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s)) return Number(s);
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    try {
      return s.startsWith('"') ? (JSON.parse(s) as string) : s.slice(1, -1).replace(/''/g, "'");
    } catch {
      return s.slice(1, -1);
    }
  }
  return s;
}

function stripComment(line: string): string {
  let q: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === q) q = null;
    } else if (c === '"' || c === "'") q = c;
    else if (c === '#' && (i === 0 || /\s/.test(line[i - 1]))) return line.slice(0, i);
  }
  return line;
}

/** Parses the flat recipe YAML and validates it against the schema. */
export function fromYaml(text: string, schema: RecipeSchema, lang: 'es' | 'en'): { recipe: Recipe } | { error: YamlError } {
  const out: Recipe = {};
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = stripComment(lines[i]).replace(/\s+$/, '');
    if (!line.trim()) continue;
    const m = /^([A-Za-z_][\w]*)\s*:\s*(.*)$/.exec(line);
    if (!m || /^\s/.test(line)) {
      return { error: { line: i + 1, message: lang === 'es' ? 'se esperaba «clave: valor» (la receta v0.1 es plana)' : 'expected "key: value" (the v0.1 recipe is flat)' } };
    }
    const [, key, rawVal] = m;
    const val = parseScalar(rawVal);
    const f = schema[key];
    if (!f) return { error: { line: i + 1, message: lang === 'es' ? `clave desconocida «${key}»` : `unknown key "${key}"` } };
    if (isNumeric(f)) {
      if (typeof val !== 'number') return { error: { line: i + 1, message: lang === 'es' ? `«${key}» debe ser un número` : `"${key}" must be a number` } };
      if ((f.min !== undefined && val < f.min) || (f.max !== undefined && val > f.max))
        return {
          error: { line: i + 1, message: lang === 'es' ? `«${key}» debe estar entre ${f.min} y ${f.max}` : `"${key}" must be between ${f.min} and ${f.max}` },
        };
    } else if (f.options && !f.options.includes(val as string)) {
      return { error: { line: i + 1, message: lang === 'es' ? `«${key}» debe ser ${f.options.join(' | ')}` : `"${key}" must be ${f.options.join(' | ')}` } };
    }
    out[key] = val;
  }
  return { recipe: out };
}

/** A shell command that reproduces the render through the engine's HTTP API
 *  (a dedicated render CLI is not published yet). The token is read from the
 *  environment, never embedded. */
export function cliCommand(r: Recipe, opts: { engineUrl: string; photoIds: string[]; batch: boolean; overrides?: Overrides }): string {
  const ov = opts.batch ? batchOverrides(opts.overrides, opts.photoIds) : {};
  const body = opts.batch
    ? { photo_ids: opts.photoIds, recipe: cleanRecipe(r), ...(Object.keys(ov).length ? { overrides: ov } : {}) }
    : { photo_id: opts.photoIds[0], recipe: cleanRecipe(r) };
  const json = JSON.stringify(body).replace(/'/g, `'\\''`);
  const path = opts.batch ? '/api/develop/batch' : '/api/develop/sample';
  return `curl -s -X POST '${opts.engineUrl.replace(/\/+$/, '')}${path}' -H "X-Revelado-Token: $REVELADO_TOKEN" -H 'Content-Type: application/json' -d '${json}'`;
}
