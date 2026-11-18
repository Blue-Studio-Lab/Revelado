// SPDX-License-Identifier: GPL-3.0-or-later
// Typed client for the Revelado engine — HTTP API v0.1 (see engine/README.md).
// All calls go to the same-origin /api prefix. In development the Vite server
// proxies /api to the engine (REVELADO_ENGINE_URL) and injects the engine token,
// so no secret ever reaches page scripts or browser storage.

import { safeUrl } from './safe';

export type Flag = 'pick' | 'reject' | null;

export interface Health {
  ok: boolean;
  version: string;
  rawtherapee: boolean;
  exiftool: boolean;
  ai: { provider: string | null; key_configured?: boolean } | null;
  /** Optional capability flags (see capabilities.ts). */
  capabilities?: Record<string, unknown> | null;
  develop_engine?: { available?: boolean; engine?: string | null; raw?: boolean; reason?: string | null; films?: string[]; [k: string]: unknown } | null;
  limitations?: unknown[];
  /** The user's home folder (to expand "~/" before sending a path). */
  home?: string;
  [k: string]: unknown;
}

export interface OpenLibraryResult {
  session_id: string;
  count: number;
  pairs: number;
  raw_only: number;
  jpg_only: number;
  restored?: boolean;
  path?: string;
  name?: string;
  [k: string]: unknown;
}

export interface SessionInfo {
  session_id: string;
  path: string;
  name: string;
  count: number;
  opened_at: string | number | null;
}

export interface TechnicalCull {
  sharpness_af?: number | null;
  sharpness_face?: number | null;
  sharpness_global?: number | null;
  faces?: number | null;
  eyes_open?: boolean | null;
  exposure?: { mean?: number; clip_hi_pct?: number; clip_lo_pct?: number; verdict?: string } | null;
  burst_id?: string | number | null;
  burst_size?: number | null;
  burst_best?: boolean;
  score?: number | null;
  verdict?: 'keep' | 'maybe' | 'reject' | string;
  reasons?: string[];
  [k: string]: unknown;
}

export interface ClaudeCull {
  verdict?: 'hero' | 'keep' | 'maybe' | 'reject' | string;
  score?: number | null;
  reasons?: string[];
  confidence?: string | null;
  [k: string]: unknown;
}

export interface CullInfo {
  technical?: TechnicalCull | null;
  claude?: ClaudeCull | null;
  [k: string]: unknown;
}

export interface Photo {
  id: string;
  name: string;
  raw_path: string | null;
  jpg_path: string | null;
  has_raw: boolean;
  has_jpg?: boolean;
  /** Per-photo override from the engine: can this photo be developed? */
  can_develop?: boolean;
  develop_mode?: 'raw' | 'jpg' | null;
  /** Why the engine won't develop it (code like "needs_raw", or a message). */
  develop_block_reason?: string | null;
  camera: string | null;
  lens: string | null;
  iso: number | null;
  shutter: string | number | null;
  aperture: number | string | null;
  focal: number | string | null;
  datetime: string | null;
  width: number | null;
  height: number | null;
  rating: number;
  flag: Flag;
  picture_profile?: string | null;
  is_log?: boolean;
  orientation?: number | null;
  burst_id?: string | number | null;
  cull?: CullInfo | null;
  outputs?: number;
  pending_manual?: unknown[];
  [k: string]: unknown;
}

export interface Profile {
  camera_profile: Record<string, unknown> | string | null;
  lens_profile: Record<string, unknown> | string | null;
  recommended: Record<string, unknown> | null;
  [k: string]: unknown;
}

export type MaskKind = 'noise' | 'ca' | 'shading';

export interface Film {
  id: string;
  name: string;
  stock: string;
  type: 'color' | 'bw' | string;
  scans: number;
  tier: string;
  swatch_url: string | null;
  [k: string]: unknown;
}

export interface RecipePreset {
  id: string;
  label_es?: string;
  label_en?: string;
  recipe: Recipe;
  default?: boolean;
  [k: string]: unknown;
}

/** Where the "before" image came from (`X-Preview-Source`). `raw_full` = full-resolution neutral render of the RAW
 * (only the 100 % crop of a RAW whose embedded preview is smaller than the photo). */
export type PreviewSource = 'camera_jpg' | 'embedded' | 'quick_render' | 'raw_full';

export interface BatchResult {
  job_id: string;
  recipe?: Recipe;
  /** Resolved recipes of the photos that had overrides. */
  recipes?: Record<string, Recipe>;
  [k: string]: unknown;
}

export type RecipeValue = string | number | boolean | null;
/** Flat v0.1 recipe: {film, film_strength, exposure_ev, grain, skin_retouch,
 *  blemishes, hair, accent, reframe, sharpen, nr, ca} (+ any key the engine adds). */
export type Recipe = Record<string, RecipeValue>;

export interface RecipeFieldSchema {
  type?: 'number' | 'int' | 'float' | 'enum' | 'string' | 'bool' | 'boolean' | string;
  min?: number;
  max?: number;
  step?: number;
  options?: (string | number | null)[];
  default?: RecipeValue;
  label_es?: string;
  label_en?: string;
  supported?: boolean;
  note?: string | null;
  [k: string]: unknown;
}
export type RecipeSchema = Record<string, RecipeFieldSchema>;

export type JobState = 'queued' | 'running' | 'done' | 'error' | 'cancelled';
export type JobKind = 'sample' | 'batch' | 'export' | 'cull_technical' | 'cull_claude' | string;

export interface JobOutput {
  photo_id: string;
  output_id?: string;
  url?: string;
  web_url?: string;
  path?: string;
  [k: string]: unknown;
}

export interface Job {
  job_id?: string;
  kind?: JobKind;
  state: JobState;
  progress: number;
  stage: string | null;
  eta_s: number | null;
  outputs: JobOutput[];
  log: string[];
  items_done?: number;
  items_failed?: number;
  errors?: { photo_id?: string; code?: string; message?: string }[];
  created_at?: string | number | null;
  started_at?: string | number | null;
  finished_at?: string | number | null;
  [k: string]: unknown;
}

export interface CullEstimate {
  frames: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_usd: number;
  models: unknown;
  [k: string]: unknown;
}

export interface PhotoOutput {
  output_id: string;
  job_id: string;
  recipe: Recipe | null;
  created_at: string | number | null;
  url: string;
  web_url: string;
  size?: [number, number];
  pending_manual?: unknown[];
}

export interface ExportOptions {
  photo_ids: string[];
  dest_dir: string;
  format: 'jpg' | 'tiff16';
  quality: number;
  long_edge: number | null;
  naming: 'original' | 'original_film' | 'sequence';
  recipe?: Recipe | null;
  develop_missing?: boolean;
}

export type EngineEvent =
  | { type: 'hello'; version?: string }
  | { type: 'job_progress'; job_id: string; kind?: JobKind; progress?: number; stage?: string; eta_s?: number; photo_id?: string }
  | { type: 'job_done'; job_id: string; kind?: JobKind; state?: JobState; outputs?: JobOutput[] }
  | { type: 'job_log'; job_id: string; line: string }
  | { type: 'photo_updated'; photo_id: string; rating?: number; flag?: Flag; cull?: CullInfo | null };

export class ApiError extends Error {
  status: number;
  code: string | null;
  messageEs: string | null;
  messageEn: string | null;
  retryable: boolean;
  constructor(status: number, message: string, code: string | null = null, es: string | null = null, en: string | null = null, retryable = false) {
    super(message);
    this.status = status;
    this.code = code;
    this.messageEs = es;
    this.messageEn = en;
    this.retryable = retryable;
  }
  /** The engine could not be reached at all (or the proxy answered for it). */
  get offline(): boolean {
    return this.status === 0 || this.status === 502 || this.status === 504;
  }
  localized(lang: 'es' | 'en'): string {
    if (lang === 'en' && this.messageEn) return this.messageEn;
    if (this.messageEs) return lang === 'es' ? this.messageEs : this.messageEn || this.messageEs;
    return this.message;
  }
}

let BASE = '/api';
/** Optional override (Settings → Engine). '' keeps the same-origin proxy. */
export function setApiBase(base: string): void {
  BASE = base ? base.replace(/\/+$/, '') + '/api' : '/api';
}

type ReqInit = Omit<RequestInit, 'body'> & { json?: unknown; timeoutMs?: number };

async function request<T>(path: string, init: ReqInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(init.json);
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init.timeoutMs ?? 30000);
  let res: Response;
  try {
    res = await fetch(BASE + path, { ...init, headers, body, signal: init.signal ?? ctrl.signal });
  } catch (e) {
    throw new ApiError(0, (e as Error).name === 'AbortError' ? 'timeout' : 'network');
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw await toApiError(res);
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

async function toApiError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => '');
  let msg = `HTTP ${res.status}`;
  try {
    const j = JSON.parse(text);
    const err = j && typeof j.error === 'object' ? j.error : null;
    const detail = typeof j.detail === 'string' ? j.detail : null;
    msg = detail || err?.message || JSON.stringify(j.detail ?? j);
    return new ApiError(res.status, msg, err?.code ?? null, err?.message_es ?? detail, err?.message_en ?? null, !!err?.retryable);
  } catch {
    if (text && text.length < 300) msg = text;
    return new ApiError(res.status, msg);
  }
}

const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : '';
};
const enc = encodeURIComponent;

export type Display = 'auto' | 'camera';

export const api = {
  health: () => request<Health>('/health', { timeoutMs: 4000 }),

  openLibrary: (path: string, rescan = true) =>
    request<OpenLibraryResult>('/library/open', { method: 'POST', json: { path, rescan }, timeoutMs: 300000 }),
  sessions: () => request<SessionInfo[]>('/sessions', { timeoutMs: 8000 }),
  photos: (sessionId: string) => request<Photo[]>(`/photos${q({ session_id: sessionId })}`, { timeoutMs: 60000 }),

  thumbUrl: (id: string, size = 400, display: Display = 'auto') =>
    `${BASE}/photos/${enc(id)}/thumb${q({ size, display: display === 'auto' ? undefined : display })}`,
  previewUrl: (id: string, size = 2048, display: Display = 'auto') =>
    `${BASE}/photos/${enc(id)}/preview${q({ size, display: display === 'auto' ? undefined : display })}`,
  /** Coordinates are full-resolution pixels of the oriented image; max 2048×2048 per request. */
  cropUrl: (id: string, x: number, y: number, w: number, h: number, display: Display = 'auto') =>
    `${BASE}/photos/${enc(id)}/crop${q({
      x: Math.round(x),
      y: Math.round(y),
      w: Math.round(w),
      h: Math.round(h),
      display: display === 'auto' ? undefined : display,
    })}`,

  /** Fetches the preview as a blob so X-Preview-Source can be read. */
  preview: async (id: string, size = 2048, signal?: AbortSignal): Promise<{ url: string; source: PreviewSource | null }> => {
    let res: Response;
    try {
      res = await fetch(`${BASE}/photos/${enc(id)}/preview${q({ size })}`, { signal });
    } catch (e) {
      throw new ApiError(0, (e as Error).name === 'AbortError' ? 'aborted' : 'network');
    }
    if (!res.ok) throw await toApiError(res);
    const h = (res.headers.get('X-Preview-Source') || '').trim().toLowerCase();
    const source = h === 'camera_jpg' || h === 'embedded' || h === 'quick_render' || h === 'raw_full' ? (h as PreviewSource) : null;
    const blob = await res.blob();
    return { url: URL.createObjectURL(blob), source };
  },

  exif: (id: string) => request<Record<string, unknown>>(`/photos/${enc(id)}/exif`),
  profile: (id: string) => request<Profile>(`/photos/${enc(id)}/profile`),
  cull: (id: string) => request<CullInfo>(`/photos/${enc(id)}/cull`),
  outputs: (id: string) => request<PhotoOutput[]>(`/photos/${enc(id)}/outputs`),

  /** Fetches the overlay as a blob so the X-Mask-Stats header can be read. */
  mask: async (id: string, kind: MaskKind, size = 1600, signal?: AbortSignal) => {
    let res: Response;
    try {
      res = await fetch(`${BASE}/photos/${enc(id)}/mask/${kind}${q({ size })}`, { signal });
    } catch (e) {
      throw new ApiError(0, (e as Error).name === 'AbortError' ? 'aborted' : 'network');
    }
    if (!res.ok) throw await toApiError(res);
    const raw = res.headers.get('X-Mask-Stats');
    let stats: Record<string, unknown> | null = null;
    if (raw) {
      try {
        stats = JSON.parse(raw);
      } catch {
        stats = { raw };
      }
    }
    const blob = await res.blob();
    return { url: URL.createObjectURL(blob), stats };
  },

  setRating: (id: string, rating: number, flag: Flag) =>
    request<unknown>(`/photos/${enc(id)}/rating`, { method: 'POST', json: { rating, flag } }),

  cullAuto: (sessionId: string, mode: 'technical' | 'claude', opts: { photo_ids?: string[]; allow_cloud?: boolean } = {}) =>
    request<{ job_id: string }>('/cull/auto', { method: 'POST', json: { session_id: sessionId, mode, ...opts } }),
  cullEstimate: (sessionId: string, photoIds?: string[]) =>
    request<CullEstimate>(`/cull/estimate${q({ session_id: sessionId, photo_ids: photoIds?.join(',') })}`),

  films: () => request<Film[]>('/films'),
  swatchUrl: (id: string) => `${BASE}/films/${enc(id)}/swatch`,

  defaultRecipe: () => request<Recipe>('/recipe/default'),
  recipeSchema: () => request<RecipeSchema>('/recipe/schema'),
  recipePresets: () => request<RecipePreset[]>('/recipe/presets'),
  sample: (photoId: string, recipe: Recipe) =>
    request<{ job_id: string }>('/develop/sample', { method: 'POST', json: { photo_id: photoId, recipe } }),
  /** `overrides`: {photo_id: partial recipe} merged over `recipe` for that photo only. */
  batch: (photoIds: string[], recipe: Recipe, overrides?: Record<string, Recipe>) =>
    request<BatchResult>('/develop/batch', {
      method: 'POST',
      json: overrides && Object.keys(overrides).length ? { photo_ids: photoIds, recipe, overrides } : { photo_ids: photoIds, recipe },
    }),

  job: (jobId: string) => request<Job>(`/jobs/${enc(jobId)}`, { timeoutMs: 8000 }),
  jobs: (active = false) => request<Job[]>(`/jobs${q({ active: active ? 1 : undefined })}`, { timeoutMs: 8000 }),
  cancelJob: (jobId: string) => request<{ ok: boolean }>(`/jobs/${enc(jobId)}/cancel`, { method: 'POST' }),

  exportPhotos: (opts: ExportOptions) => request<{ job_id: string }>('/export', { method: 'POST', json: opts }),

  /** Normalises an engine-relative URL (e.g. "/api/outputs/…") for use in <img>. */
  assetUrl: (u: string | null | undefined): string | null => {
    if (!u) return null;
    if (/^(https?:|blob:|data:)/i.test(u)) return u;
    if (u.startsWith('/api/')) return BASE + u.slice(4);
    if (u.startsWith('/')) return BASE + u;
    return `${BASE}/${u}`;
  },

  /**
   * Like assetUrl, but for URLs that end up in an href / src / window.open: only
   * http(s) URLs on the app's own origin or on the configured engine's origin are
   * returned; javascript:, data:, foreign hosts, etc. give undefined.
   */
  safeAssetUrl: (u: string | null | undefined): string | undefined => {
    if (typeof location === 'undefined') return undefined;
    const origins = [location.origin];
    if (/^https?:/i.test(BASE)) {
      try {
        origins.push(new URL(BASE).origin);
      } catch {
        /* ignore a malformed override */
      }
    }
    const raw = typeof u === 'string' && /^[a-z][a-z0-9+.-]*:/i.test(u.trim()) ? u : api.assetUrl(u);
    return safeUrl(raw, location.href, origins);
  },

  eventsUrl: () => {
    if (/^https?:/i.test(BASE)) return BASE.replace(/^http/i, 'ws') + '/events';
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}${BASE}/events`;
  },
};

declare const __ENGINE_URL__: string;
export const ENGINE_URL: string = typeof __ENGINE_URL__ === 'string' ? __ENGINE_URL__ : 'http://127.0.0.1:8790';
