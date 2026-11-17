// SPDX-License-Identifier: GPL-3.0-or-later
// Develop / Cuarto oscuro: preset + film picker, one
// sample first (before/after against the camera JPG or, for RAW-only photos, the
// RAW preview), recipe controls + YAML kept in sync, optional per-photo
// adjustments ("Solo esta foto", sent as batch overrides), then the batch goes
// to the Oven.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ENGINE_URL, type Film, type PreviewSource, type Recipe, type RecipeFieldSchema, type RecipePreset, type RecipeSchema, type RecipeValue } from '../api';
import { Badge, Button, EmptyState, InlineAlert, ProgressBar, Segmented, Skeleton, Soon, copyText, isTyping } from '../components/ui';
import { useI18n } from '../i18n';
import {
  FALLBACK_PRESETS,
  FALLBACK_SCHEMA,
  GROUPS,
  batchOverrides,
  cleanRecipe,
  cliCommand,
  defaultsOf,
  diffRecipe,
  effectiveRecipe,
  fromYaml,
  isNumeric,
  matchPreset,
  normalisePresets,
  presetLabel,
  toYaml,
  visibleSchema,
  withOverride,
  type YamlError,
} from '../recipe';
import { navigate } from '../router';
import { canDevelop, developability, hasJpg, reasonText } from '../capabilities';
import type { Photo } from '../api';
import { recipeKey, useDevelopPhotos, useStore, type SampleVersion } from '../store';

type ViewMode = 'split' | 'side' | 'toggle';

export function tierBadge(f: Film, tt: (es: string, en: string) => string) {
  switch (f.tier) {
    case 'spec':
      return <Badge tone="ok" icon="✓">{tt('Calibrado', 'Calibrated')}</Badge>;
    case 'silver_candidate':
      return <Badge icon="~">{tt('Estimado · plata', 'Estimated · silver')}</Badge>;
    case 'bronze':
      return <Badge icon="~">{tt('Estimado · bronce', 'Estimated · bronze')}</Badge>;
    case 'provisional':
      return <Badge tone="warn" icon="β">{tt('Provisional', 'Provisional')}</Badge>;
    case 'builtin':
      return <Badge tone="ok" icon="✓">{tt('Incluido', 'Built-in')}</Badge>;
    default:
      return <Badge icon="·">{tt('Sin escaneos', 'No scans')}</Badge>;
  }
}

export const NO_FILM = 'none';
export const filmUsable = (f: Film) => f.id === NO_FILM || f.engine_supported === true || f.status === 'trained';

/** The engine lists "none" first; older engines (or an empty list) get it added so neutral is always offered. */
export function withNoFilm(films: Film[]): Film[] {
  const none = films.find((f) => f.id === NO_FILM) ?? { id: NO_FILM, name: 'none', stock: NO_FILM, type: 'neutral', scans: 0, tier: 'builtin', swatch_url: null, engine_supported: true, status: 'trained' };
  return [none, ...films.filter((f) => f.id !== NO_FILM)];
}

function PresetPicker({ presets, recipe, onPick }: { presets: RecipePreset[]; recipe: Recipe; onPick: (p: RecipePreset) => void }) {
  const { t, lang } = useI18n();
  const current = matchPreset(recipe, presets);
  return (
    <div className="preset-picker" data-testid="preset-picker">
      <div className="preset-options" role="radiogroup" aria-label={t('dark.preset')}>
        {presets.map((p) => {
          const on = current?.id === p.id;
          return (
            <button
              type="button"
              key={p.id}
              role="radio"
              aria-checked={on}
              data-preset={p.id}
              className={`preset-btn${on ? ' is-selected' : ''}`}
              title={(lang === 'es' ? p.label_es : p.label_en) || undefined}
              onClick={() => onPick(p)}
            >
              {presetLabel(p, lang)}
            </button>
          );
        })}
      </div>
      <span className="field-help">{current ? t('dark.preset.help') : <Badge icon="✎">{t('dark.preset.custom')}</Badge>}</span>
    </div>
  );
}

function FilmPicker({ films, value, onChange, loading, error, onRetry }: { films: Film[]; value: RecipeValue; onChange: (id: string) => void; loading: boolean; error: string | null; onRetry: () => void }) {
  const { t, tt } = useI18n();
  const list = withNoFilm(films);
  return (
    <div className="film-picker" role="radiogroup" aria-label={t('dark.film')}>
      {error && (
        <InlineAlert tone="error" action={<Button size="sm" onClick={onRetry}>{t('common.retry')}</Button>}>
          {error}
        </InlineAlert>
      )}
      {list.map((f) => {
        if (f.id === NO_FILM) {
          const selected = value === NO_FILM;
          return (
            <button
              type="button"
              key={f.id}
              role="radio"
              aria-checked={selected}
              data-film={NO_FILM}
              className={`film-card${selected ? ' is-selected' : ''}`}
              onClick={() => onChange(NO_FILM)}
              title={t('dark.neutral.help')}
            >
              <div className="film-swatch neutral" aria-hidden="true">
                —
              </div>
              <div className="film-info">
                <span className="film-name">{t('dark.neutral')}</span>
                <span className="muted small">{t('dark.neutral.help')}</span>
              </div>
            </button>
          );
        }
        const usable = filmUsable(f);
        const selected = value === f.id;
        return (
          <button
            type="button"
            key={f.id}
            role="radio"
            aria-checked={selected}
            aria-disabled={!usable || undefined}
            data-film={f.id}
            className={`film-card${selected ? ' is-selected' : ''}${usable ? '' : ' is-disabled'}`}
            onClick={() => usable && onChange(f.id)}
            title={usable ? f.name : tt('Perfil aún no entrenado: se podrá revelar cuando se entrene con tus escaneos.', 'Profile not trained yet: it will be developable once trained from your scans.')}
          >
            <div className="film-swatch" aria-hidden="true">
              {f.swatch_url ? <img src={api.assetUrl(f.swatch_url) ?? ''} alt="" loading="lazy" /> : <span>{f.type === 'bw' ? 'B&N' : '◐'}</span>}
            </div>
            <div className="film-info">
              <span className="film-name">{f.name}</span>
              <span className="film-badges">
                {tierBadge(f, tt)}
                {f.type === 'bw' && <Badge>{t('films.bw')}</Badge>}
              </span>
              <span className="muted small">
                {usable ? `✓ ${tt('Revelado disponible', 'Development available')}` : `${tt('Perfil aún no entrenado', 'Profile not trained yet')} · ${t('common.soon')}`}
              </span>
            </div>
          </button>
        );
      })}
      {loading && list.length === 1 && [0, 1].map((k) => <Skeleton key={`sk${k}`} w="100%" h={64} />)}
    </div>
  );
}

function Control({
  name,
  f,
  value,
  onChange,
  defaultValue,
  overridden = false,
}: {
  name: string;
  f: RecipeFieldSchema;
  value: RecipeValue;
  onChange: (v: RecipeValue) => void;
  defaultValue: RecipeValue;
  overridden?: boolean;
}) {
  const { lang, t, tt, num } = useI18n();
  const ovMark = overridden ? (
    <span className="ov-mark" title={t('dark.ov.badge')} aria-label={t('dark.ov.badge')}>
      ◆
    </span>
  ) : null;
  const label = (lang === 'es' ? f.label_es : f.label_en) || name;
  const unsupported = f.supported === false;
  const id = `ctl-${name}`;
  const optLabel = (o: string | number | null) => {
    const m: Record<string, [string, string]> = {
      off: ['Off', 'Off'],
      auto: ['Auto', 'Auto'],
      suave: ['Suave', 'Soft'],
      medio: ['Medio', 'Medium'],
      fuerte: ['Fuerte', 'Strong'],
      soft: ['Suave', 'Soft'],
      strong: ['Fuerte', 'Strong'],
      suggest: ['Sugerir', 'Suggest'],
      apply: ['Aplicar', 'Apply'],
    };
    const e = m[String(o)];
    return e ? (lang === 'es' ? e[0] : e[1]) : String(o);
  };
  if (isNumeric(f)) {
    const v = typeof value === 'number' ? value : Number(defaultValue ?? 0);
    const digits = (f.step ?? 1) < 1 ? ((f.step ?? 1) < 0.1 ? 2 : 1) : 0;
    const shown = name === 'exposure_ev' ? `${v > 0 ? '+' : ''}${num(v, 1)} EV` : name === 'film_strength' ? `${Math.round(v * 100)} %` : num(v, digits);
    return (
      <div className={`adjust${unsupported ? ' is-unsupported' : ''}${overridden ? ' is-overridden' : ''}`} data-field={name}>
        <label htmlFor={id} className="adjust-label" onDoubleClick={() => onChange(defaultValue)} title={tt('Doble clic: restablecer', 'Double-click: reset')}>
          {label}
          {v !== defaultValue && <span className="dot" aria-label={tt('modificado', 'modified')}>•</span>}
          {ovMark}
        </label>
        <output htmlFor={id} className="num adjust-value">
          {shown}
        </output>
        <input
          id={id}
          type="range"
          min={f.min}
          max={f.max}
          step={f.step ?? 1}
          value={v}
          disabled={unsupported}
          onChange={(e) => onChange(Number(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === 'Backspace' || e.key === 'Delete') {
              e.preventDefault();
              onChange(defaultValue);
            }
          }}
          aria-valuetext={`${label} ${shown}`}
        />
        {f.note && <span className="field-help">{f.note}</span>}
      </div>
    );
  }
  const options = (f.options ?? []).filter((o): o is string => typeof o === 'string');
  return (
    <div className={`adjust${unsupported ? ' is-unsupported' : ''}${overridden ? ' is-overridden' : ''}`} data-field={name}>
      <span className="adjust-label" onDoubleClick={() => onChange(defaultValue)}>
        {label}
        {value !== defaultValue && <span className="dot">•</span>}
        {ovMark}
        {unsupported && <Soon why={f.note ?? undefined} />}
      </span>
      <Segmented
        size="sm"
        label={label}
        value={String(value ?? defaultValue)}
        onChange={(v) => onChange(v)}
        options={options.map((o) => ({ value: o, label: optLabel(o), disabled: unsupported && o !== defaultValue }))}
      />
      {f.note && <span className="field-help">{f.note}</span>}
    </div>
  );
}

function YamlPanel({ recipe, schema, onApply }: { recipe: Recipe; schema: RecipeSchema; onApply: (r: Recipe) => void }) {
  const { t, lang } = useI18n();
  const canonical = useMemo(() => toYaml(recipe, schema, lang), [recipe, schema, lang]);
  const [text, setText] = useState(canonical);
  const [err, setErr] = useState<YamlError | null>(null);
  const editing = useRef(false);
  useEffect(() => {
    if (!editing.current) {
      setText(canonical);
      setErr(null);
    }
  }, [canonical]);
  return (
    <div className="yaml-panel">
      <textarea
        className="yaml-editor mono"
        value={text}
        spellCheck={false}
        aria-label={t('dark.yaml')}
        aria-describedby="yaml-status"
        aria-invalid={!!err}
        rows={14}
        onFocus={() => (editing.current = true)}
        onBlur={() => {
          editing.current = false;
          if (!err) setText(canonical);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur();
        }}
        onChange={(e) => {
          const v = e.target.value;
          setText(v);
          const res = fromYaml(v, schema, lang);
          if ('error' in res) setErr(res.error);
          else {
            setErr(null);
            onApply({ ...recipe, ...res.recipe });
          }
        }}
      />
      <p id="yaml-status" className={err ? 'field-error' : 'field-help'} role="status">
        {err ? t('dark.yaml.invalid', { line: err.line, msg: err.message }) : `${t('dark.yaml.valid')} · ${t('dark.yaml.help')}`}
      </p>
    </div>
  );
}

/** The "before" image, fetched as a blob so X-Preview-Source tells whether it is the camera JPG or a RAW preview. */
function useBeforeImage(photo: Photo | undefined): { src: string | null; source: PreviewSource | null } {
  const id = photo?.id;
  const [state, setState] = useState<{ id: string | null; src: string | null; source: PreviewSource | null }>({ id: null, src: null, source: null });
  useEffect(() => {
    if (!id) return;
    const ctrl = new AbortController();
    let blobUrl: string | null = null;
    setState({ id, src: null, source: null });
    api
      .preview(id, 2048, ctrl.signal)
      .then((r) => {
        blobUrl = r.url;
        if (!ctrl.signal.aborted) setState({ id, src: r.url, source: r.source });
        else URL.revokeObjectURL(r.url);
      })
      .catch(() => {
        // fall back to the plain URL (the <img> shows the engine's own error state)
        if (!ctrl.signal.aborted) setState({ id, src: api.previewUrl(id, 2048), source: null });
      });
    return () => {
      ctrl.abort();
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [id]);
  if (!photo || state.id !== photo.id) return { src: null, source: null };
  // Engines without the header: a photo with no camera JPG can only show a RAW preview.
  const source = state.source ?? (state.src && !hasJpg(photo) ? 'embedded' : state.src ? 'camera_jpg' : null);
  return { src: state.src, source };
}

function BeforeAfter({
  before,
  after,
  mode,
  afterLabel,
  beforeLabel,
  beforeSource,
  beforeTitle,
}: {
  before: string;
  after: string | null;
  mode: ViewMode;
  afterLabel: string;
  beforeLabel: string;
  beforeSource: PreviewSource | null;
  beforeTitle?: string;
}) {
  const { t } = useI18n();
  // the visible "before" caption says where the image comes from (camera JPG or RAW preview)
  const bl = { 'data-testid': 'before-label', 'data-source': beforeSource ?? undefined, title: beforeTitle };
  const [pos, setPos] = useState(50);
  const [showAfter, setShowAfter] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      if (e.key === '\\') {
        e.preventDefault();
        setShowAfter((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const setFromX = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    setPos(Math.max(0, Math.min(100, ((clientX - r.left) / r.width) * 100)));
  };
  if (!after || mode === 'side') {
    return (
      <div className="ba-side">
        <figure>
          <img src={before} alt={beforeLabel} />
          <figcaption {...bl}>{beforeLabel}</figcaption>
        </figure>
        <figure>
          {after ? <img src={after} alt={afterLabel} /> : <div className="ba-placeholder">{t('dark.noSample')}</div>}
          <figcaption>{afterLabel}</figcaption>
        </figure>
      </div>
    );
  }
  if (mode === 'toggle') {
    return (
      <div className="ba-toggle">
        <button type="button" className="ba-toggle-btn" onClick={() => setShowAfter((v) => !v)} aria-pressed={showAfter}>
          <img src={showAfter ? after : before} alt={showAfter ? afterLabel : beforeLabel} />
        </button>
        <span className="viewer-chip" {...(showAfter ? {} : bl)}>
          {showAfter ? afterLabel : beforeLabel} · \
        </span>
      </div>
    );
  }
  return (
    <div
      className="ba-split"
      ref={box}
      onPointerDown={(e) => {
        dragging.current = true;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        setFromX(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && setFromX(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
    >
      <img className="ba-img" src={after} alt={afterLabel} draggable={false} />
      <div className="ba-before" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}>
        <img className="ba-img" src={before} alt={beforeLabel} draggable={false} />
      </div>
      <div
        className="ba-handle"
        style={{ left: `${pos}%` }}
        role="slider"
        tabIndex={0}
        aria-label={t('dark.split')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pos)}
        aria-valuetext={`${Math.round(pos)} %`}
        onKeyDown={(e) => {
          const step = e.shiftKey ? 20 : 5;
          if (e.key === 'ArrowLeft') setPos((p) => Math.max(0, p - step));
          else if (e.key === 'ArrowRight') setPos((p) => Math.min(100, p + step));
          else if (e.key === 'Home') setPos(0);
          else if (e.key === 'End') setPos(100);
          else return;
          e.preventDefault();
          e.stopPropagation();
        }}
      >
        <span className="ba-grip" aria-hidden="true">
          ◀▶
        </span>
      </div>
      <span className="ba-label left" {...bl}>
        {beforeLabel}
      </span>
      <span className="ba-label right">{afterLabel}</span>
    </div>
  );
}

function BlockedList({ photos }: { photos: Photo[] }) {
  const s = useStore();
  const { t } = useI18n();
  if (!photos.length) return null;
  return (
    <div className="blocked-list" data-testid="blocked-list">
      <InlineAlert tone="warn">
        <span>{t('dev.blockedList', { n: photos.length })}</span>
        <ul>
          {photos.map((p) => (
            <li key={p.id} data-photo={p.id}>
              <span className="mono">{p.name}</span> — {reasonText(developability(p, s.caps).reason, t)}
            </li>
          ))}
        </ul>
      </InlineAlert>
    </div>
  );
}

export function Develop() {
  const s = useStore();
  const { t, tt } = useI18n();
  const all = useDevelopPhotos();
  const photos = useMemo(() => all.filter((p) => canDevelop(p, s.caps)), [all, s.caps]);
  const blocked = useMemo(() => all.filter((p) => !canDevelop(p, s.caps)), [all, s.caps]);
  const [films, setFilms] = useState<Film[]>([]);
  const [filmsState, setFilmsState] = useState<'loading' | 'ok' | 'error'>('loading');
  const [schema, setSchema] = useState<RecipeSchema>(FALLBACK_SCHEMA);
  const [defaults, setDefaults] = useState<Recipe>(() => defaultsOf(FALLBACK_SCHEMA));
  const [mode, setMode] = useState<ViewMode>('split');
  const [panel, setPanel] = useState<'controls' | 'yaml'>('controls');
  const [starting, setStarting] = useState(false);
  const [batching, setBatching] = useState(false);
  const [viewV, setViewV] = useState<number | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [presets, setPresets] = useState<RecipePreset[]>(() => normalisePresets(FALLBACK_PRESETS));
  /** "Solo esta foto": edits go to the sample photo's override instead of the batch recipe. */
  const [thisOnly, setThisOnly] = useState(false);

  const loadFilms = useCallback(() => {
    setFilmsState('loading');
    api
      .films()
      .then((f) => {
        setFilms(f);
        setFilmsState('ok');
      })
      .catch(() => setFilmsState('error'));
  }, []);

  useEffect(() => {
    loadFilms();
    api.recipeSchema().then((sc) => sc && typeof sc === 'object' && Object.keys(sc).length && setSchema(visibleSchema(sc))).catch(() => undefined);
    api
      .recipePresets()
      .then((list) => setPresets(normalisePresets(list)))
      .catch(() => undefined);
    api
      .defaultRecipe()
      .then((raw) => {
        const r = cleanRecipe(raw);
        setDefaults(r);
        if (!s.ui.recipe) s.setUi({ recipe: r });
      })
      .catch((e) => {
        if (!s.ui.recipe) s.setUi({ recipe: defaultsOf(FALLBACK_SCHEMA) });
        s.errorToast(e, undefined, t('err.recipe'));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.session?.session_id]);

  /** The batch recipe (what every photo gets unless it has its own adjustments). */
  const recipe: Recipe = useMemo(() => cleanRecipe(s.ui.recipe ?? defaults), [s.ui.recipe, defaults]);
  const overrides = useMemo(() => s.ui.overrides ?? {}, [s.ui.overrides]);
  const setRecipe = useCallback((r: Recipe) => s.setUi({ recipe: cleanRecipe(r) }), [s]);

  const sample = photos.find((p) => p.id === s.ui.sampleId) ?? photos[0];
  useEffect(() => {
    if (sample && s.ui.sampleId !== sample.id) s.setUi({ sampleId: sample.id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sample?.id]);
  const before = useBeforeImage(sample);

  const sampleOverride = sample ? overrides[sample.id] : undefined;
  const hasOverride = (id: string) => !!overrides[id] && Object.keys(overrides[id]).length > 0;
  // Opening a photo that already has its own adjustments shows them; other photos start on the batch recipe.
  useEffect(() => {
    setThisOnly(!!sample && hasOverride(sample.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sample?.id]);

  /** The recipe the sample photo is developed with (batch recipe + its override). */
  const sampleRecipe = useMemo(() => effectiveRecipe(recipe, overrides, sample?.id), [recipe, overrides, sample?.id]);
  /** What the controls, YAML, preset and film pickers show and edit. */
  const shownRecipe = thisOnly ? sampleRecipe : recipe;
  const setShown = useCallback(
    (next: Recipe) => {
      if (thisOnly && sample) s.setUi((u) => ({ overrides: withOverride(u.overrides, sample.id, diffRecipe(cleanRecipe(next), cleanRecipe(u.recipe ?? defaults))) }));
      else setRecipe(next);
    },
    [thisOnly, sample, s, defaults, setRecipe],
  );
  const setField = (k: string, v: RecipeValue) => setShown({ ...shownRecipe, [k]: v });
  const clearOverride = () => sample && s.setUi((u) => ({ overrides: withOverride(u.overrides, sample.id, {}) }));
  const rk = recipeKey(sampleRecipe);

  // sample versions of the current photo
  const versions = useMemo(() => s.ui.samples.filter((v) => v.photoId === sample?.id), [s.ui.samples, sample?.id]);
  const latest = versions[versions.length - 1];
  const latestJob = latest ? s.jobStatus[latest.jobId] : undefined;
  const running = !!latestJob && (latestJob.state === 'queued' || latestJob.state === 'running');

  // fold finished job outputs into the version list
  useEffect(() => {
    s.ui.samples.forEach((v) => {
      const j = s.jobStatus[v.jobId];
      if (j && j.state === 'done' && !v.outputs.length && j.outputs.length) {
        const secs =
          j.started_at && j.finished_at ? (new Date(String(j.finished_at)).getTime() - new Date(String(j.started_at)).getTime()) / 1000 : (Date.now() - v.at) / 1000;
        s.setUi((u) => ({ samples: u.samples.map((x) => (x.jobId === v.jobId ? { ...x, outputs: j.outputs, seconds: isFinite(secs) && secs > 0 ? secs : null } : x)) }));
        setViewV(null);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.jobStatus]);

  const shown: SampleVersion | undefined = viewV != null ? versions.find((v) => v.v === viewV) : [...versions].reverse().find((v) => v.outputs.length);
  const afterUrl = shown?.outputs[0] ? api.assetUrl(shown.outputs[0].web_url || shown.outputs[0].url) : null;
  const filmName = (id: RecipeValue) => (id === NO_FILM ? t('dark.neutral') : films.find((f) => f.id === id)?.name ?? String(id ?? '—'));
  // A finished sample approves the batch when it still matches the recipe its photo would get now.
  const approved = s.ui.samples.some((v) => v.outputs.length > 0 && v.recipeKey === recipeKey(effectiveRecipe(recipe, overrides, v.photoId)));
  const anyDone = versions.some((v) => v.outputs.length);
  const stale = anyDone && latest && latest.recipeKey !== rk;
  const batchActive = s.activeJobs.some((j) => j.kind === 'batch');
  const filmOk = sampleRecipe.film === NO_FILM || films.length === 0 || films.some((f) => f.id === sampleRecipe.film && filmUsable(f));
  const overrideIds = photos.filter((p) => hasOverride(p.id)).map((p) => p.id);

  const developSample = useCallback(async () => {
    if (!sample) return;
    setStarting(true);
    try {
      const { job_id } = await api.sample(sample.id, sampleRecipe);
      const v = (versions[versions.length - 1]?.v ?? 0) + 1;
      s.setUi((u) => ({ samples: [...u.samples, { v, jobId: job_id, photoId: sample.id, recipe: sampleRecipe, recipeKey: rk, outputs: [], seconds: null, at: Date.now() }] }));
      s.trackJob({ id: job_id, kind: 'sample', label: `${t('oven.kind.sample')} ${sample.name} v${v}`, createdAt: Date.now(), photoIds: [sample.id] });
      setViewV(null);
    } catch (e) {
      s.errorToast(e, developSample, t('err.job', { msg: '' }).replace(/:\s*$/, ':'));
    } finally {
      setStarting(false);
    }
  }, [sample, sampleRecipe, rk, versions, s, t]);

  const developBatch = useCallback(async () => {
    setBatching(true);
    try {
      const ids = photos.map((p) => p.id);
      const ov = batchOverrides(overrides, ids);
      const nOv = Object.keys(ov).length;
      const { job_id } = await api.batch(ids, recipe, ov);
      const label = `${t('oven.kind.batch')} · ${ids.length} · ${filmName(recipe.film)}${nOv ? ` · ${t('dark.ov.count', { n: nOv })}` : ''}`;
      s.trackJob({ id: job_id, kind: 'batch', label, createdAt: Date.now(), photoIds: ids });
      navigate('oven');
    } catch (e) {
      s.errorToast(e, developBatch, t('err.job', { msg: '' }).replace(/:\s*$/, ':'));
    } finally {
      setBatching(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photos, recipe, overrides, s, t, films]);

  // shortcuts (develop)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || s.shortcutsOpen || s.exportOpen) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.shiftKey && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        if (!running && filmOk) developSample();
      } else if (mod && e.key === 'Enter') {
        e.preventDefault();
        if (approved && !batchActive) developBatch();
      } else if (!mod && (e.key === 'y' || e.key === 'Y')) {
        e.preventDefault();
        setMode(e.shiftKey ? 'side' : 'split');
      } else if (!mod && e.key === '\\') setMode((m) => (m === 'toggle' ? m : 'toggle'));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [developSample, developBatch, running, approved, batchActive, filmOk, s.shortcutsOpen, s.exportOpen]);

  if (!s.session) {
    return <EmptyState icon="▢" title={t('lib.empty.title')} body={t('lib.empty.body')} action={<Button variant="primary" onClick={() => navigate('library')}>{t('nav.library')}</Button>} />;
  }
  if (!photos.length) {
    return (
      <EmptyState
        icon="◐"
        title={t('dark.empty')}
        body={blocked.length ? <BlockedList photos={blocked} /> : undefined}
        action={<Button variant="primary" onClick={() => navigate('cull')}>{t('dark.goCull')}</Button>}
      />
    );
  }

  const eta = photos.length * (latest?.seconds ?? 47);
  const cli = cliCommand(recipe, { engineUrl: ENGINE_URL, photoIds: photos.map((p) => p.id), batch: true, overrides });
  const notCameraJpg = before.source === 'embedded' || before.source === 'quick_render';
  const beforeLabel = notCameraJpg ? t('dark.before.raw') : t('dark.before');
  const ovKeys = Object.keys(sampleOverride ?? {});

  return (
    <div className="workspace develop">
      <aside className="panel panel-left" aria-label={t('dark.film')}>
        <div className="panel-heading">
          <h3>{t('dark.preset')}</h3>
        </div>
        <PresetPicker presets={presets} recipe={shownRecipe} onPick={(p) => setShown({ ...shownRecipe, ...p.recipe })} />
        <div className="panel-heading">
          <h3>{t('dark.film')}</h3>
        </div>
        <FilmPicker
          films={films}
          value={shownRecipe.film}
          onChange={(id) => setField('film', id)}
          loading={filmsState === 'loading'}
          error={filmsState === 'error' ? t('err.films') : null}
          onRetry={loadFilms}
        />
      </aside>

      <section className="stage" aria-label={t('dark.sample')}>
        <div className="toolbar" role="toolbar" aria-label={t('dark.title')}>
          <h1 className="title">{t('dark.title')}</h1>
          <span className="muted">{t('dark.photosInRoom', { n: photos.length })}</span>
          {blocked.length > 0 && <Badge tone="warn" icon="▲" title={t('dev.needsRaw')}>{t('cull.sendNoRaw', { n: blocked.length })}</Badge>}
          <span className="sep">·</span>
          <span>
            {t('dark.sample')}: <strong className="mono">{sample?.name}</strong>
          </span>
          {ovKeys.length > 0 && (
            <Badge tone="accent" icon="◆" title={t('dark.ov.keys', { keys: ovKeys.join(', ') })}>
              <span data-testid="sample-override-badge">{t('dark.ov.badge')}</span>
            </Badge>
          )}
          {overrideIds.length > 0 && <span className="muted small" data-testid="override-count">{t('dark.ov.count', { n: overrideIds.length })}</span>}
          <Button size="sm" variant="ghost" onClick={() => setPickerOpen((v) => !v)} aria-expanded={pickerOpen}>
            {t('dark.changeSample')}
          </Button>
          <span className="grow" />
          <Segmented
            size="sm"
            label={tt('Vista', 'View')}
            value={mode}
            onChange={setMode}
            options={[
              { value: 'split', label: `${t('dark.view.split')} (Y)` },
              { value: 'side', label: t('dark.view.side') },
              { value: 'toggle', label: `${t('dark.view.toggle')} (\\)` },
            ]}
          />
        </div>
        <BlockedList photos={blocked} />
        {pickerOpen && (
          <div className="sample-strip" role="listbox" aria-label={t('dark.pickSample')}>
            {photos.map((p) => (
              <button
                type="button"
                role="option"
                aria-selected={p.id === sample?.id}
                key={p.id}
                data-photo={p.id}
                data-override={hasOverride(p.id) ? 'yes' : undefined}
                className={`strip-thumb${p.id === sample?.id ? ' is-selected' : ''}${hasOverride(p.id) ? ' has-override' : ''}`}
                onClick={() => {
                  s.setUi({ sampleId: p.id });
                  setViewV(null);
                  setPickerOpen(false);
                }}
                title={hasOverride(p.id) ? `${p.name} · ${t('dark.ov.badge')}` : p.name}
              >
                <img src={api.thumbUrl(p.id, 200)} alt={p.name} loading="lazy" />
                {hasOverride(p.id) && (
                  <span className="ov-dot" aria-label={t('dark.ov.badge')}>
                    ◆
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
        <div className="sample-stage">
          {sample && before.src ? (
            <BeforeAfter
              before={before.src}
              after={afterUrl}
              mode={mode}
              beforeLabel={beforeLabel}
              beforeSource={before.source}
              beforeTitle={before.source === 'quick_render' ? t('dark.before.quick') : before.source === 'embedded' ? t('dark.before.embedded') : undefined}
              afterLabel={t('dark.after', { film: filmName(shown?.recipe.film ?? sampleRecipe.film) }) + (shown ? ` · v${shown.v}` : '')}
            />
          ) : (
            <Skeleton w="100%" h={360} />
          )}
        </div>
        <div className="sample-controls">
          <Button
            variant={approved ? 'secondary' : 'primary'}
            onClick={developSample}
            loading={starting || running}
            disabled={running || !filmOk || s.healthState !== 'ok'}
            why={!filmOk ? tt('Esta película aún no tiene perfil entrenado.', 'This film stock has no trained profile yet.') : running ? t('dark.developing') : undefined}
          >
            {running ? t('dark.developing') : t('dark.develop')}
          </Button>
          <span className="soon-btn">
            <Button disabled why={t('dark.quick.soon')}>
              {t('dark.quick')}
            </Button>
            <Soon why={t('dark.quick.soon')} />
          </span>
          {running && latestJob && (
            <div className="job-inline">
              <ProgressBar
                value={latestJob.progress ?? 0}
                indeterminate={latestJob.state === 'queued'}
                label={t('dark.developing')}
                valueText={`${Math.round((latestJob.progress ?? 0) * 100)} %${latestJob.eta_s ? ` · ${s.prefs.lang === 'es' ? 'quedan' : 'left'} ${Math.round(latestJob.eta_s)} s` : ''}`}
              />
              <span className="muted small">
                {latestJob.state === 'queued' ? t('dark.queued') : `${latestJob.stage ?? ''} · ${Math.round((latestJob.progress ?? 0) * 100)} %${latestJob.eta_s ? ` · ≈ ${Math.round(latestJob.eta_s)} s` : ''}`}
              </span>
              <Button size="sm" variant="ghost" onClick={() => api.cancelJob(latest!.jobId).then(() => s.refreshJob(latest!.jobId)).catch((e) => s.errorToast(e))}>
                {t('common.cancel')}
              </Button>
            </div>
          )}
        </div>
        {latestJob?.state === 'error' && (
          <InlineAlert tone="error" action={<Button size="sm" onClick={developSample}>{t('common.retry')}</Button>}>
            {t('dark.error')} {latestJob.errors?.[0]?.message ?? latestJob.log?.[latestJob.log.length - 1] ?? ''}
          </InlineAlert>
        )}
        {latestJob?.state === 'done' && latestJob.log?.some((l) => /ignor/i.test(l)) && (
          <InlineAlert tone="info">{latestJob.log.filter((l) => /ignor/i.test(l)).join(' · ')}</InlineAlert>
        )}
        {!anyDone && !running && <p className="muted pad">{t('dark.noSample')}</p>}
        {stale && <InlineAlert tone="warn">{t('dark.stale')}</InlineAlert>}
        {versions.length > 0 && (
          <div className="versions" role="list" aria-label={t('dark.history')}>
            <span className="muted">{t('dark.history')}:</span>
            {versions.map((v) => {
              const j = s.jobStatus[v.jobId];
              const state = v.outputs.length ? '✓' : j?.state === 'error' ? '⊘' : j?.state === 'cancelled' ? '–' : '…';
              return (
                <button
                  type="button"
                  role="listitem"
                  key={v.jobId}
                  className={`chip-btn${shown?.v === v.v ? ' is-on' : ''}`}
                  aria-pressed={shown?.v === v.v}
                  disabled={!v.outputs.length}
                  onClick={() => setViewV(v.v)}
                  title={v.seconds ? t('dark.sampleDone', { t: s.prefs.lang === 'es' ? `${Math.round(v.seconds)} s` : `${Math.round(v.seconds)} s`, v: v.v }) : undefined}
                >
                  v{v.v} {state}
                </button>
              );
            })}
            {shown?.outputs[0] && (
              <a className="small-link" href={api.assetUrl(shown.outputs[0].url) ?? '#'} target="_blank" rel="noreferrer">
                {tt('Abrir a resolución completa ↗', 'Open full resolution ↗')}
              </a>
            )}
          </div>
        )}
        <div className="develop-foot">
          <Button
            variant="primary"
            size="lg"
            onClick={developBatch}
            loading={batching}
            disabled={!approved || batchActive || s.healthState !== 'ok'}
            why={batchActive ? t('dark.batch.busy') : !approved ? t('dark.batch.disabled') : undefined}
          >
            {t('dark.batch', { n: photos.length })}
          </Button>
          <span className="muted">{t('dark.batch.eta', { t: s.prefs.lang === 'es' ? `${Math.round(eta / 60)} min` : `${Math.round(eta / 60)} min` })}</span>
        </div>
      </section>

      <aside className="panel panel-right" aria-label={t('dark.recipe')}>
        <div className="panel-heading">
          <h3>{t('dark.recipe')}</h3>
          <Segmented
            size="sm"
            label={t('dark.recipe')}
            value={panel}
            onChange={setPanel}
            options={[
              { value: 'controls', label: tt('Controles', 'Controls') },
              { value: 'yaml', label: 'YAML' },
            ]}
          />
        </div>
        <div className="mode-toggle">
          <Segmented
            size="sm"
            label="Manual / Auto"
            value="manual"
            onChange={() => undefined}
            options={[
              { value: 'manual', label: t('dark.manual') },
              { value: 'auto', label: `✨ ${t('dark.auto')}`, disabled: true, title: s.aiProvider ? t('dark.auto.soon') : t('dark.auto.noKey') },
            ]}
          />
          <span className="field-help">{s.aiProvider ? t('dark.auto.soon') : t('dark.auto.noKey')}</span>
        </div>
        <div className={`scope-toggle${thisOnly ? ' is-on' : ''}`}>
          <label className="switch">
            <input type="checkbox" role="switch" aria-checked={thisOnly} checked={thisOnly} onChange={(e) => setThisOnly(e.target.checked)} data-testid="scope-this" />
            <span>{t('dark.scope.this')}</span>
            {sample && <span className="mono small muted">{sample.name}</span>}
          </label>
          <span className="field-help">
            {thisOnly ? t('dark.scope.on', { name: sample?.name ?? '' }) : ovKeys.length ? t('dark.scope.baseNote') : t('dark.scope.off')}
          </span>
          {ovKeys.length > 0 && (
            <Button size="sm" variant="ghost" onClick={clearOverride}>
              {t('dark.ov.clear')}
            </Button>
          )}
        </div>
        {panel === 'controls' ? (
          <div className="adjust-groups">
            {GROUPS.map((g) => (
              <section key={g.id} className="panel-section">
                <h4 className="heading">{t(`dark.g.${g.id}` as 'dark.g.film')}</h4>
                {g.fields
                  .filter((k) => schema[k])
                  .map((k) => (
                    <Control
                      key={k}
                      name={k}
                      f={schema[k]}
                      value={shownRecipe[k] ?? null}
                      defaultValue={defaults[k] ?? schema[k].default ?? null}
                      onChange={(v) => setField(k, v)}
                      overridden={thisOnly && !!sampleOverride && k in sampleOverride}
                    />
                  ))}
              </section>
            ))}
          </div>
        ) : (
          <YamlPanel recipe={shownRecipe} schema={schema} onApply={setShown} />
        )}
        <div className="row pad">
          {thisOnly ? (
            <Button size="sm" variant="ghost" onClick={clearOverride} disabled={!ovKeys.length}>
              {t('dark.ov.clear')}
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setRecipe(defaults)}>
              {t('dark.resetAll')}
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => copyText(cli).then((ok) => s.toast({ kind: ok ? 'success' : 'error', text: ok ? tt('Comando copiado (usa la API del motor; exporta REVELADO_TOKEN).', 'Command copied (uses the engine API; export REVELADO_TOKEN).') : 'Clipboard ✗' }))}
            title={cli}
          >
            {t('dark.cli')}
          </Button>
        </div>
        <p className="field-help pad">{tt('Un CLI de revelado llegará próximamente; el comando copiado reproduce el lote (con los ajustes por foto) con la API del motor.', 'A develop CLI is coming soon; the copied command reproduces the batch (with per-photo adjustments) through the engine API.')}</p>
      </aside>
    </div>
  );
}
