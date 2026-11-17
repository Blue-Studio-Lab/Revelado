// SPDX-License-Identifier: GPL-3.0-or-later
// Library / Import. Browsers can't open native folder pickers,
// so the source is a path field + recent folders (local list + engine catalogue).
import { useEffect, useMemo, useState } from 'react';
import { api, type SessionInfo } from '../api';
import { GridSkeleton, ThumbGrid } from '../components/ThumbGrid';
import { Button, EmptyState, InlineAlert } from '../components/ui';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore } from '../store';

import { pairOf, type PairKind as PairKey } from '../capabilities';

export function FolderForm({ autoFocus }: { autoFocus?: boolean }) {
  const s = useStore();
  const { t } = useI18n();
  const [path, setPath] = useState(s.prefs.lastPath ?? '');
  useEffect(() => {
    if (s.prefs.lastPath) setPath(s.prefs.lastPath);
  }, [s.prefs.lastPath]);
  return (
    <form
      className="folder-form"
      onSubmit={(e) => {
        e.preventDefault();
        s.openFolder(path);
      }}
    >
      <label className="field">
        <span className="heading">{t('lib.path')}</span>
        <input
          className="input mono"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder={t('lib.pathPh')}
          spellCheck={false}
          autoFocus={autoFocus}
          aria-describedby="lib-path-help"
        />
      </label>
      <Button
        type="submit"
        variant="primary"
        disabled={!path.trim() || s.healthState !== 'ok'}
        loading={s.openState === 'loading'}
        why={s.healthState !== 'ok' ? t('err.offline') : undefined}
      >
        {s.openState === 'loading' ? t('lib.opening') : t('lib.open')}
      </Button>
      <p id="lib-path-help" className="field-help">
        {t('lib.pathHelp')}
      </p>
    </form>
  );
}

function Recents() {
  const s = useStore();
  const { t, tt } = useI18n();
  const [engineSessions, setEngineSessions] = useState<SessionInfo[]>([]);
  useEffect(() => {
    if (s.healthState !== 'ok') return;
    api.sessions().then(setEngineSessions).catch(() => setEngineSessions([]));
  }, [s.healthState, s.session?.session_id]);
  const items = useMemo(() => {
    const map = new Map<string, { path: string; count?: number }>();
    s.prefs.recents.forEach((p) => map.set(p, { path: p }));
    engineSessions.forEach((e) => map.set(e.path, { path: e.path, count: e.count }));
    return [...map.values()].slice(0, 10);
  }, [s.prefs.recents, engineSessions]);
  if (!items.length) return null;
  return (
    <section className="panel-section">
      <div className="panel-heading">
        <h3>{t('lib.recent')}</h3>
      </div>
      <ul className="recent-list">
        {items.map((r) => (
          <li key={r.path} className={s.prefs.lastPath === r.path && s.session ? 'is-current' : ''}>
            <button type="button" className="link-btn mono" title={r.path} onClick={() => s.openFolder(r.path, { rescan: false })} disabled={s.openState === 'loading'}>
              {r.path.split('/').filter(Boolean).slice(-2).join('/')}
              {r.count != null && <span className="muted"> · {r.count}</span>}
            </button>
            {s.prefs.recents.includes(r.path) && (
              <button
                type="button"
                className="icon-btn sm"
                aria-label={`${t('lib.removeRecent')}: ${r.path}`}
                title={t('lib.removeRecent')}
                onClick={() => s.setPrefs({ recents: s.prefs.recents.filter((x) => x !== r.path) })}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      {s.session && (
        <Button size="sm" variant="ghost" onClick={() => s.prefs.lastPath && s.openFolder(s.prefs.lastPath, { rescan: true })}>
          ↻ {tt('Volver a leer la carpeta', 'Rescan folder')}
        </Button>
      )}
    </section>
  );
}

export function Library() {
  const s = useStore();
  const { t, tt } = useI18n();
  const [pairs, setPairs] = useState<Record<PairKey, boolean>>({ both: true, raw: true, jpg: true });
  const [cams, setCams] = useState<Record<string, boolean>>({});
  const [minRating, setMinRating] = useState(0);
  const [flag, setFlag] = useState<'all' | 'pick' | 'reject' | 'none'>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<'date' | 'name' | 'rating'>('date');
  const [focus, setFocus] = useState(0);
  const [jpgAlert, setJpgAlert] = useState(true);

  const counts = useMemo(() => {
    const c = { both: 0, raw: 0, jpg: 0 } as Record<PairKey, number>;
    const cam = new Map<string, number>();
    s.photos.forEach((p) => {
      c[pairOf(p)]++;
      const k = [p.camera, p.lens].filter(Boolean).join(' · ') || '—';
      cam.set(k, (cam.get(k) ?? 0) + 1);
    });
    return { pair: c, cam: [...cam.entries()].sort((a, b) => b[1] - a[1]) };
  }, [s.photos]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = s.photos.filter((p) => {
      if (!pairs[pairOf(p)]) return false;
      const k = [p.camera, p.lens].filter(Boolean).join(' · ') || '—';
      if (cams[k] === false) return false;
      if (p.rating < minRating) return false;
      if (flag === 'pick' && p.flag !== 'pick') return false;
      if (flag === 'reject' && p.flag !== 'reject') return false;
      if (flag === 'none' && p.flag) return false;
      if (q && !p.name.toLowerCase().includes(q)) return false;
      return true;
    });
    const sorted = [...list];
    if (sort === 'name') sorted.sort((a, b) => a.name.localeCompare(b.name));
    else if (sort === 'rating') sorted.sort((a, b) => b.rating - a.rating || a.name.localeCompare(b.name));
    else sorted.sort((a, b) => String(a.datetime ?? '').localeCompare(String(b.datetime ?? '')) || a.name.localeCompare(b.name));
    return sorted;
  }, [s.photos, pairs, cams, minRating, flag, search, sort]);

  useEffect(() => {
    if (focus >= filtered.length) setFocus(Math.max(0, filtered.length - 1));
  }, [filtered.length, focus]);

  const filtersActive = !pairs.both || !pairs.raw || !pairs.jpg || Object.values(cams).some((v) => v === false) || minRating > 0 || flag !== 'all' || !!search;
  const clearFilters = () => {
    setPairs({ both: true, raw: true, jpg: true });
    setCams({});
    setMinRating(0);
    setFlag('all');
    setSearch('');
  };

  const picks = s.photos.filter((p) => p.flag === 'pick');

  let body;
  if (!s.session) {
    body =
      s.openState === 'loading' ? (
        <GridSkeleton size={s.prefs.thumbSize} />
      ) : (
        <EmptyState
          icon="▢"
          title={t('lib.empty.title')}
          body={
            <>
              {t('lib.empty.body')}
              {s.sessionError && <span className="field-error block">{s.sessionError}</span>}
            </>
          }
        />
      );
  } else if (s.photosState === 'loading' && !s.photos.length) {
    body = <GridSkeleton size={s.prefs.thumbSize} />;
  } else if (s.photosState === 'error' && !s.photos.length) {
    body = <EmptyState icon="⊘" title={t('err.offline')} action={<Button onClick={() => s.reloadPhotos()}>{t('common.retry')}</Button>} />;
  } else if (!s.photos.length) {
    body = <EmptyState icon="▢" title={t('lib.none.title')} body={t('lib.none.body')} />;
  } else if (!filtered.length) {
    body = <EmptyState icon="⌕" title={t('lib.filtered.title')} action={<Button onClick={clearFilters}>{t('lib.clearFilters')}</Button>} />;
  } else {
    body = (
      <ThumbGrid
        photos={filtered}
        size={s.prefs.thumbSize}
        focusIndex={focus}
        setFocusIndex={setFocus}
        onOpen={(p) => navigate(`viewer/${encodeURIComponent(p.id)}`)}
        onRate={(p, patch) => s.rate(p.id, patch)}
        autoAdvance={s.prefs.autoAdvance}
        showCull
        label={t('lib.title')}
      />
    );
  }

  return (
    <div className="workspace">
      <aside className="panel panel-left" aria-label={t('lib.title')}>
        <section className="panel-section">
          <FolderForm autoFocus={!s.session} />
          <p className="field-help">🔒 {t('lib.originals')}</p>
        </section>
        <Recents />
        {s.session && s.photos.length > 0 && (
          <>
            <section className="panel-section">
              <div className="panel-heading">
                <h3>{t('lib.pairing')}</h3>
              </div>
              {(['both', 'raw', 'jpg'] as PairKey[]).map((k) => (
                <label key={k} className="check facet">
                  <input type="checkbox" checked={pairs[k]} onChange={(e) => setPairs({ ...pairs, [k]: e.target.checked })} />
                  <span>
                    {t(k === 'both' ? 'lib.pair.both' : k === 'raw' ? 'lib.pair.raw' : 'lib.pair.jpg')}
                    {k === 'jpg' && counts.pair.jpg > 0 && <span className="warn"> ▲</span>}
                  </span>
                  <span className="num muted">{counts.pair[k]}</span>
                </label>
              ))}
            </section>
            <section className="panel-section">
              <div className="panel-heading">
                <h3>{t('lib.cameraLens')}</h3>
              </div>
              {counts.cam.map(([k, n]) => (
                <label key={k} className="check facet">
                  <input type="checkbox" checked={cams[k] !== false} onChange={(e) => setCams({ ...cams, [k]: e.target.checked })} />
                  <span className="ellipsis" title={k}>
                    {k}
                  </span>
                  <span className="num muted">{n}</span>
                </label>
              ))}
            </section>
            <section className="panel-section">
              <div className="panel-heading">
                <h3>{t('lib.filters')}</h3>
              </div>
              <label className="field-inline">
                <span>{t('lib.minRating')}</span>
                <select className="input sm" value={minRating} onChange={(e) => setMinRating(Number(e.target.value))}>
                  {[0, 1, 2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>
                      {n === 0 ? '—' : '★'.repeat(n)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-inline">
                <span>{t('lib.flag')}</span>
                <select className="input sm" value={flag} onChange={(e) => setFlag(e.target.value as typeof flag)}>
                  <option value="all">{t('lib.flag.all')}</option>
                  <option value="pick">{t('lib.flag.pick')}</option>
                  <option value="reject">{t('lib.flag.reject')}</option>
                  <option value="none">{t('lib.flag.none')}</option>
                </select>
              </label>
              {filtersActive && (
                <Button size="sm" variant="ghost" onClick={clearFilters}>
                  {t('lib.clearFilters')}
                </Button>
              )}
            </section>
          </>
        )}
      </aside>

      <section className="stage" aria-label={t('lib.title')}>
        <div className="toolbar" role="toolbar" aria-label={t('lib.title')}>
          <h1 className="title">{t('lib.title')}</h1>
          {s.session && (
            <span className="muted">
              {t('lib.summary', { count: s.session.count, pairs: s.session.pairs, raw: s.session.raw_only, jpg: s.session.jpg_only })}
            </span>
          )}
          <span className="grow" />
          {s.photos.length > 0 && (
            <>
              <input
                className="input sm search"
                type="search"
                placeholder={t('lib.search')}
                aria-label={t('lib.search')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <label className="field-inline">
                <span>{t('lib.sort')}</span>
                <select className="input sm" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
                  <option value="date">{t('lib.sort.date')}</option>
                  <option value="name">{t('lib.sort.name')}</option>
                  <option value="rating">{t('lib.sort.rating')}</option>
                </select>
              </label>
              <label className="field-inline">
                <span>{t('lib.size')}</span>
                <input
                  type="range"
                  min={96}
                  max={320}
                  step={8}
                  value={s.prefs.thumbSize}
                  onChange={(e) => s.setPrefs({ thumbSize: Number(e.target.value) })}
                  aria-valuetext={`${s.prefs.thumbSize} px`}
                />
              </label>
              <Button
                size="sm"
                onClick={() => s.setExportOpen(picks.map((p) => p.id))}
                disabled={!picks.length}
                why={tt('Marca fotos como elegidas (P) para exportarlas.', 'Mark photos as picks (P) to export them.')}
              >
                {t('lib.export')}
              </Button>
            </>
          )}
        </div>
        {s.session && s.session.jpg_only > 0 && jpgAlert && (
          <InlineAlert
            tone="warn"
            onDismiss={() => setJpgAlert(false)}
            action={
              <Button size="sm" variant="ghost" onClick={() => setPairs({ both: false, raw: false, jpg: true })}>
                {tt('Ver', 'Show')}
              </Button>
            }
          >
            {t('lib.jpgOnly', { n: s.session.jpg_only })}
          </InlineAlert>
        )}
        <div className="stage-scroll">{body}</div>
        {s.photos.length > 0 && (
          <div className="stage-foot muted">
            {t('lib.showing', { n: filtered.length, total: s.photos.length })} · {t('cull.keys')}
          </div>
        )}
      </section>
    </div>
  );
}
