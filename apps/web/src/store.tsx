// SPDX-License-Identifier: GPL-3.0-or-later
// App state: engine health, the open folder (session), photos, jobs, toasts,
// and per-session UI state (develop selection, recipe, sample versions, job
// history). The engine's catalogue is the source of truth for ratings, flags,
// cull results and developed outputs; the UI only remembers what the engine
// doesn't (which job ids belong to this folder, the recipe being edited…).
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  api,
  ApiError,
  setApiBase,
  type EngineEvent,
  type Flag,
  type Health,
  type Job,
  type JobKind,
  type JobOutput,
  type OpenLibraryResult,
  type Photo,
  type Recipe,
} from './api';
import { canDevelop, engineCaps, type EngineCaps } from './capabilities';
import { I18nProvider, makeI18n } from './i18n';
import { loadPrefs, savePrefs, type Prefs } from './prefs';
import { emptyRecord, getEntry, isSafeKey, withEntry } from './safe';
import { readItem, sessionKey, writeItem } from './storage';

// ───────────────────────── types

export interface Toast {
  id: number;
  kind: 'info' | 'success' | 'error';
  text: string;
  actionLabel?: string;
  action?: () => void;
}

export interface TrackedJob {
  id: string;
  kind: JobKind;
  label: string;
  createdAt: number;
  photoIds?: string[];
}

export interface SampleVersion {
  v: number;
  jobId: string;
  photoId: string;
  recipe: Recipe;
  recipeKey: string;
  outputs: JobOutput[];
  seconds: number | null;
  at: number;
}

export interface SessionUi {
  developIds: string[] | null; // null = "use picks"
  sampleId: string | null;
  recipe: Recipe | null;
  /** Per-photo partial recipes ("Solo esta foto"), sent as `overrides` with the batch. */
  overrides: Record<string, Recipe>;
  samples: SampleVersion[];
  jobs: TrackedJob[];
}

const emptyUi = (): SessionUi => ({ developIds: null, sampleId: null, recipe: null, overrides: {}, samples: [], jobs: [] });

export type HealthState = 'checking' | 'ok' | 'offline';
export type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export function recipeKey(r: Recipe | null | undefined): string {
  if (!r) return '';
  return JSON.stringify(Object.keys(r).sort().map((k) => [k, r[k]]));
}

const ACTIVE = (s: string | undefined) => s === 'queued' || s === 'running';

interface Store {
  prefs: Prefs;
  setPrefs: (patch: Partial<Prefs>) => void;

  health: Health | null;
  healthState: HealthState;
  refreshHealth: () => Promise<void>;
  aiProvider: string | null;
  caps: EngineCaps;

  session: OpenLibraryResult | null;
  sessionError: string | null;
  openState: LoadState;
  openFolder: (path: string, opts?: { rescan?: boolean; quiet?: boolean }) => Promise<boolean>;
  closeFolder: () => void;
  photos: Photo[];
  photosState: LoadState;
  reloadPhotos: () => Promise<void>;
  photoById: (id: string) => Photo | undefined;
  rate: (id: string, patch: { rating?: number; flag?: Flag }) => void;

  ui: SessionUi;
  setUi: (patch: Partial<SessionUi> | ((u: SessionUi) => Partial<SessionUi>)) => void;

  jobStatus: Record<string, Job | null>;
  trackJob: (j: TrackedJob) => void;
  refreshJob: (id: string) => Promise<Job | null>;
  activeJobs: TrackedJob[];
  wsConnected: boolean;

  toasts: Toast[];
  toast: (t: Omit<Toast, 'id'>) => void;
  dismissToast: (id: number) => void;
  errorToast: (e: unknown, retry?: () => void, prefix?: string) => void;
  errText: (e: unknown) => string;

  shortcutsOpen: boolean;
  setShortcutsOpen: (v: boolean) => void;
  exportOpen: string[] | null; // photo ids to export, or null when closed
  setExportOpen: (ids: string[] | null) => void;
}

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error('StoreProvider missing');
  return s;
}

function loadUi(sid: string): SessionUi {
  try {
    const raw = readItem(sessionKey(sid));
    if (raw) {
      const saved = JSON.parse(raw) as Partial<SessionUi>;
      // Sessions saved before per-photo overrides kept the old default recipe (Ektar): start them on the
      // engine's current default (neutral) instead.
      if (!saved.overrides || typeof saved.overrides !== 'object') return { ...emptyUi(), ...saved, recipe: null, overrides: {} };
      return { ...emptyUi(), ...saved };
    }
  } catch {
    /* ignore */
  }
  return emptyUi();
}
function saveUi(sid: string, ui: SessionUi) {
  writeItem(sessionKey(sid), JSON.stringify({ ...ui, jobs: ui.jobs.slice(-60), samples: ui.samples.slice(-20) }));
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefsState] = useState<Prefs>(() => loadPrefs());
  const i18n = useMemo(() => makeI18n(prefs.lang), [prefs.lang]);

  const setPrefs = useCallback((patch: Partial<Prefs>) => {
    setPrefsState((p) => {
      const next = { ...p, ...patch };
      savePrefs(next);
      return next;
    });
  }, []);

  // reflect prefs on <html>
  useEffect(() => {
    const el = document.documentElement;
    el.lang = prefs.lang;
    el.dataset.surround = prefs.surround;
    el.dataset.reduceMotion = String(prefs.reduceMotion);
    el.style.fontSize = `${Math.round(prefs.uiScale * 100)}%`;
    el.style.setProperty('--ui-scale', String(prefs.uiScale));
    setApiBase(prefs.apiBase);
  }, [prefs]);

  // ── toasts
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastSeq = useRef(1);
  const dismissToast = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const toast = useCallback((t: Omit<Toast, 'id'>) => {
    const id = toastSeq.current++;
    setToasts((ts) => [...ts.slice(-4), { ...t, id }]);
  }, []);
  const errText = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError) {
        if (e.offline) return i18n.t('err.offline');
        return e.localized(prefs.lang);
      }
      return e instanceof Error ? e.message : String(e);
    },
    [i18n, prefs.lang],
  );
  const errorToast = useCallback(
    (e: unknown, retry?: () => void, prefix?: string) => {
      const msg = errText(e);
      toast({
        kind: 'error',
        text: prefix ? `${prefix} ${msg}` : msg,
        actionLabel: retry ? i18n.t('common.retry') : undefined,
        action: retry,
      });
    },
    [errText, toast, i18n],
  );

  // ── health
  const [health, setHealth] = useState<Health | null>(null);
  const [healthState, setHealthState] = useState<HealthState>('checking');
  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      setHealthState('ok');
    } catch {
      setHealthState('offline');
    }
  }, []);
  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 15000);
    return () => clearInterval(t);
  }, [refreshHealth, prefs.apiBase]);
  const aiProvider = health?.ai?.provider ?? null;
  const caps = useMemo(() => engineCaps(health), [health]);

  // ── session + photos
  const [session, setSession] = useState<OpenLibraryResult | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [openState, setOpenState] = useState<LoadState>('idle');
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [photosState, setPhotosState] = useState<LoadState>('idle');
  const [ui, setUiState] = useState<SessionUi>(emptyUi());
  const sidRef = useRef<string | null>(null);

  const setUi = useCallback((patch: Partial<SessionUi> | ((u: SessionUi) => Partial<SessionUi>)) => {
    setUiState((u) => {
      const next = { ...u, ...(typeof patch === 'function' ? patch(u) : patch) };
      if (sidRef.current) saveUi(sidRef.current, next);
      return next;
    });
  }, []);

  const loadPhotos = useCallback(
    async (sid: string) => {
      setPhotosState('loading');
      try {
        const list = await api.photos(sid);
        if (sidRef.current !== sid) return;
        setPhotos(list);
        setPhotosState('ready');
      } catch (e) {
        if (sidRef.current !== sid) return;
        setPhotosState('error');
        errorToast(e, () => loadPhotos(sid));
      }
    },
    [errorToast],
  );
  const reloadPhotos = useCallback(async () => {
    if (sidRef.current) await loadPhotos(sidRef.current);
  }, [loadPhotos]);

  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;

  const openFolder = useCallback(
    async (path: string, opts: { rescan?: boolean; quiet?: boolean } = {}) => {
      const p = path.trim();
      if (!p) return false;
      setOpenState('loading');
      setSessionError(null);
      try {
        let res = await api.openLibrary(p, opts.rescan ?? true);
        if (res.count === 0 && opts.rescan === false) res = await api.openLibrary(p, true);
        sidRef.current = res.session_id;
        setSession(res);
        setUiState(loadUi(res.session_id));
        setOpenState('ready');
        const recents = [p, ...prefsRef.current.recents.filter((r) => r !== p)].slice(0, 8);
        setPrefs({ recents, lastPath: p });
        await loadPhotos(res.session_id);
        if (res.restored && !opts.quiet) toast({ kind: 'success', text: i18n.t('lib.restored') });
        return true;
      } catch (e) {
        setOpenState('error');
        if (!sidRef.current) setPrefs({ lastPath: p });
        const msg = errText(e);
        setSessionError(msg);
        if (!opts.quiet) errorToast(e, () => openFolder(p, opts), i18n.t('err.open', { msg: '' }).replace(/:\s*$/, ':'));
        return false;
      }
    },
    [loadPhotos, setPrefs, toast, errText, errorToast, i18n],
  );

  const closeFolder = useCallback(() => {
    sidRef.current = null;
    setSession(null);
    setPhotos([]);
    setPhotosState('idle');
    setOpenState('idle');
    setUiState(emptyUi());
    setPrefs({ lastPath: null });
  }, [setPrefs]);

  // reopen the last folder at start (restores ratings/results from the engine catalogue)
  const triedRestore = useRef(false);
  useEffect(() => {
    if (triedRestore.current || healthState !== 'ok' || session) return;
    triedRestore.current = true;
    if (prefs.lastPath) openFolder(prefs.lastPath, { rescan: false, quiet: true });
  }, [healthState, session, prefs.lastPath, openFolder]);

  const photoIndex = useMemo(() => new Map(photos.map((p) => [p.id, p])), [photos]);
  const photoById = useCallback((id: string) => photoIndex.get(id), [photoIndex]);

  const patchPhoto = useCallback((id: string, patch: Partial<Photo>) => {
    setPhotos((ps) => ps.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  }, []);

  const rate = useCallback(
    (id: string, patch: { rating?: number; flag?: Flag }) => {
      const p = photoIndex.get(id);
      if (!p) return;
      const before = { rating: p.rating, flag: p.flag };
      const next = { rating: patch.rating ?? p.rating, flag: patch.flag !== undefined ? patch.flag : p.flag };
      patchPhoto(id, next);
      const send = () =>
        api.setRating(id, next.rating, next.flag).catch((e) => {
          patchPhoto(id, before);
          errorToast(e, () => {
            patchPhoto(id, next);
            send();
          }, i18n.t('rating.saveError', { file: p.name }));
        });
      send();
    },
    [photoIndex, patchPhoto, errorToast, i18n],
  );

  // ── jobs
  const [jobStatus, setJobStatus] = useState<Record<string, Job | null>>(() => emptyRecord<Job | null>());
  const jobStatusRef = useRef(jobStatus);
  jobStatusRef.current = jobStatus;

  const onJobFinished = useCallback(
    (id: string, job: Job) => {
      const tracked = ui.jobs.find((j) => j.id === id);
      const kind = job.kind ?? tracked?.kind;
      if (job.state === 'done') {
        if (kind === 'batch') toast({ kind: 'success', text: i18n.t('oven.done', { n: job.items_done ?? job.outputs.length }) });
        if (kind === 'export')
          toast({ kind: 'success', text: i18n.tt('Exportación terminada: {n} archivos.', 'Export finished: {n} files.', { n: job.outputs.length }) });
        if (typeof kind === 'string' && kind.startsWith('cull')) toast({ kind: 'success', text: i18n.t('cull.done') });
      } else if (job.state === 'error') {
        const last = job.errors?.[job.errors.length - 1]?.message || job.log?.[job.log.length - 1] || '';
        toast({ kind: 'error', text: `${tracked?.label ?? kind ?? 'Job'}: ${last || i18n.tt('error', 'error')}` });
      }
      reloadPhotos();
    },
    [ui.jobs, toast, i18n, reloadPhotos],
  );
  const onJobFinishedRef = useRef(onJobFinished);
  onJobFinishedRef.current = onJobFinished;

  const refreshJob = useCallback(async (id: string) => {
    try {
      const j = await api.job(id);
      const prev = getEntry(jobStatusRef.current, id);
      setJobStatus((s) => withEntry(s, id, j));
      if (prev && ACTIVE(prev.state) && !ACTIVE(j.state)) onJobFinishedRef.current(id, j);
      return j;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setJobStatus((s) => withEntry<Job | null>(s, id, null));
      return null;
    }
  }, []);

  const trackJob = useCallback(
    (j: TrackedJob) => {
      setUi((u) => ({ jobs: [...u.jobs.filter((x) => x.id !== j.id), j] }));
      setJobStatus((s) => withEntry<Job | null>(s, j.id, { state: 'queued', progress: 0, stage: null, eta_s: null, outputs: [], log: [], kind: j.kind }));
      setTimeout(() => refreshJob(j.id), 300);
    },
    [setUi, refreshJob],
  );

  // load status of remembered jobs when a session opens; pick up active jobs started elsewhere
  useEffect(() => {
    if (!session) return;
    ui.jobs.forEach((j) => {
      if (getEntry(jobStatusRef.current, j.id) === undefined) refreshJob(j.id);
    });
    api
      .jobs(true)
      .then((list) => {
        list.forEach((j) => {
          if (!j.job_id) return;
          const known = ui.jobs.some((t) => t.id === j.job_id);
          setJobStatus((s) => withEntry<Job | null>(s, j.job_id, j));
          if (!known)
            setUi((u) => ({
              jobs: [...u.jobs, { id: j.job_id as string, kind: j.kind ?? 'batch', label: String(j.kind ?? 'job'), createdAt: Date.now() }],
            }));
        });
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id]);

  const activeJobs = useMemo(() => ui.jobs.filter((j) => ACTIVE(jobStatus[j.id]?.state)), [ui.jobs, jobStatus]);

  // ── live events (WebSocket) with polling fallback
  const [wsConnected, setWsConnected] = useState(false);
  useEffect(() => {
    if (healthState !== 'ok') return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      try {
        ws = new WebSocket(api.eventsUrl());
      } catch {
        retry = setTimeout(connect, 5000);
        return;
      }
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        if (!closed) retry = setTimeout(connect, 4000);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (m) => {
        let ev: EngineEvent;
        try {
          ev = JSON.parse(m.data);
        } catch {
          return;
        }
        if (ev.type === 'job_progress') {
          if (!isSafeKey(ev.job_id)) return;
          setJobStatus((s) => {
            const prev = getEntry(s, ev.job_id) ?? { state: 'running', progress: 0, stage: null, eta_s: null, outputs: [], log: [] };
            return withEntry<Job | null>(s, ev.job_id, {
              ...prev,
              state: 'running',
              progress: ev.progress ?? prev.progress,
              stage: ev.stage ?? prev.stage,
              eta_s: ev.eta_s ?? prev.eta_s,
            } as Job);
          });
        } else if (ev.type === 'job_done') {
          refreshJob(ev.job_id);
        } else if (ev.type === 'job_log') {
          if (!isSafeKey(ev.job_id)) return;
          setJobStatus((s) => {
            const prev = getEntry(s, ev.job_id);
            if (!prev) return s;
            return withEntry<Job | null>(s, ev.job_id, { ...prev, log: [...(prev.log ?? []), ev.line].slice(-400) });
          });
        } else if (ev.type === 'photo_updated') {
          const patch: Partial<Photo> = {};
          if (ev.rating !== undefined) patch.rating = ev.rating;
          if (ev.flag !== undefined) patch.flag = ev.flag;
          if (ev.cull !== undefined) patch.cull = ev.cull;
          if (Object.keys(patch).length) patchPhoto(ev.photo_id, patch);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [healthState, refreshJob, patchPhoto, prefs.apiBase]);

  // polling keeps working when the socket is down (and catches missed events)
  useEffect(() => {
    const t = setInterval(
      () => {
        Object.entries(jobStatusRef.current).forEach(([id, j]) => {
          if (j && ACTIVE(j.state)) refreshJob(id);
        });
      },
      wsConnected ? 4000 : 1500,
    );
    return () => clearInterval(t);
  }, [wsConnected, refreshJob]);

  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState<string[] | null>(null);

  const value: Store = {
    prefs,
    setPrefs,
    health,
    healthState,
    refreshHealth,
    aiProvider,
    caps,
    session,
    sessionError,
    openState,
    openFolder,
    closeFolder,
    photos,
    photosState,
    reloadPhotos,
    photoById,
    rate,
    ui,
    setUi,
    jobStatus,
    trackJob,
    refreshJob,
    activeJobs,
    wsConnected,
    toasts,
    toast,
    dismissToast,
    errorToast,
    errText,
    shortcutsOpen,
    setShortcutsOpen,
    exportOpen,
    setExportOpen,
  };

  return (
    <Ctx.Provider value={value}>
      <I18nProvider lang={prefs.lang}>{children}</I18nProvider>
    </Ctx.Provider>
  );
}

/** Photos sent to Develop: the explicit list, else the picks.
 *  Includes photos that can't be developed, so the UI can say why. */
export function useDevelopPhotos(): Photo[] {
  const { photos, ui } = useStore();
  return useMemo(() => {
    if (ui.developIds) {
      const set = new Set(ui.developIds);
      return photos.filter((p) => set.has(p.id));
    }
    return photos.filter((p) => p.flag === 'pick');
  }, [photos, ui.developIds]);
}

/** The Develop photos the running engine can actually develop. */
export function useDevelopablePhotos(): Photo[] {
  const all = useDevelopPhotos();
  const { caps } = useStore();
  return useMemo(() => all.filter((p) => canDevelop(p, caps)), [all, caps]);
}
