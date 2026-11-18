// SPDX-License-Identifier: GPL-3.0-or-later
// Oven / Horno: one photo at a time, live progress (WebSocket
// with polling fallback), finished thumbnails as they arrive, ETA, log, stop.
import { useEffect, useMemo, useState } from 'react';
import { api, type Job } from '../api';
import { Button, Dialog, EmptyState, InlineAlert, ProgressBar, Soon } from '../components/ui';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore, type TrackedJob } from '../store';

const kindKey = (k: string) =>
  k === 'sample' ? 'oven.kind.sample' : k === 'batch' ? 'oven.kind.batch' : k === 'export' ? 'oven.kind.export' : 'oven.kind.cull';

export function Oven() {
  const s = useStore();
  const { t, tt, dur } = useI18n();
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);

  const jobs = useMemo(() => [...s.ui.jobs].sort((a, b) => b.createdAt - a.createdAt), [s.ui.jobs]);
  const heavy = jobs.filter((j) => j.kind === 'batch' || j.kind === 'export' || j.kind === 'sample');
  const active = s.activeJobs.find((j) => j.kind === 'batch' || j.kind === 'export') ?? s.activeJobs.find((j) => j.kind === 'sample');
  const currentId = selected ?? active?.id ?? heavy[0]?.id ?? null;
  const tracked: TrackedJob | undefined = jobs.find((j) => j.id === currentId);
  const job: Job | null | undefined = currentId ? s.jobStatus[currentId] : undefined;

  useEffect(() => {
    if (currentId && !(currentId in s.jobStatus)) s.refreshJob(currentId);
  }, [currentId, s]);

  if (!s.session) {
    return <EmptyState icon="▢" title={t('lib.empty.title')} body={t('lib.empty.body')} action={<Button variant="primary" onClick={() => navigate('library')}>{t('nav.library')}</Button>} />;
  }
  if (!tracked) {
    return <EmptyState icon="♨" title={t('oven.idle')} body={t('oven.oneAtATime')} action={<Button variant="primary" onClick={() => navigate('develop')}>{t('oven.goDark')}</Button>} />;
  }

  const total = tracked.photoIds?.length ?? job?.outputs.length ?? 0;
  const done = job?.items_done ?? job?.outputs.length ?? 0;
  const failed = job?.items_failed ?? job?.errors?.length ?? 0;
  const isActive = job?.state === 'queued' || job?.state === 'running';
  const eta = job?.eta_s ?? null;
  const endClock = eta ? new Date(Date.now() + eta * 1000).toLocaleTimeString(s.prefs.lang === 'es' ? 'es-ES' : 'en-GB', { hour: '2-digit', minute: '2-digit' }) : null;
  const pct = Math.round((job?.progress ?? 0) * 100);
  const nameOf = (id: string) => s.photoById(id)?.name ?? id;
  const outputs = job?.outputs ?? [];
  const isExport = tracked.kind === 'export';
  // an export job reports progress in items_done before its outputs list is filled
  const finishedCount = Math.max(outputs.length, isExport ? done : 0);

  const stop = async () => {
    setConfirmStop(false);
    try {
      await api.cancelJob(tracked.id);
      s.refreshJob(tracked.id);
    } catch (e) {
      s.errorToast(e, stop);
    }
  };

  return (
    <div className="workspace no-left">
      <section className="stage oven" aria-label={t('nav.oven')}>
        <div className="oven-head">
          <h1 className="title">
            {t(kindKey(String(tracked.kind)) as 'oven.kind.batch')} ·{' '}
            {isActive
              ? t(isExport ? 'oven.title.export' : 'oven.title', { i: Math.min(done + 1, Math.max(total, 1)), n: total || '?' })
              : job?.state === 'done'
                ? t(isExport ? 'oven.done.export' : 'oven.done', { n: done })
                : job?.state === 'cancelled'
                  ? tt('Detenido · {n} terminadas', 'Stopped · {n} finished', { n: done })
                  : job?.state === 'error'
                    ? t('oven.error')
                    : job === null
                      ? t('oven.state.lost')
                      : t('common.loading')}
          </h1>
          <p className="muted">{tracked.label}</p>
          <ProgressBar
            value={job?.progress ?? 0}
            indeterminate={job?.state === 'queued'}
            label={t('nav.oven')}
            valueText={`${done} / ${total}${eta ? ` · ${t('oven.eta', { t: dur(eta) })}` : ''}`}
          />
          <div className="row">
            <span className="num">{pct} %</span>
            {isActive && eta != null && (
              <span title={endClock ? t('oven.endsAt', { clock: endClock }) : undefined}>
                {t('oven.eta', { t: dur(eta) })}
                {done > 0 && total > 0 && ` (≈ ${dur(eta / Math.max(1, total - done))}/${tt('foto', 'photo')})`}
              </span>
            )}
            {job?.state === 'queued' && <span className="muted">{t('oven.queued')}</span>}
            {failed > 0 && <span className="danger">⊘ {tt('{n} con error', '{n} failed', { n: failed })}</span>}
            <span className="grow" />
            <span className="soon-btn">
              <Button size="sm" disabled why={t('oven.pause.soon')}>
                {t('oven.pause')}
              </Button>
              <Soon why={t('oven.pause.soon')} />
            </span>
            <Button size="sm" variant="danger" disabled={!isActive} onClick={() => setConfirmStop(true)}>
              {t('oven.stop')}
            </Button>
            {tracked.kind === 'batch' && job?.state === 'done' && (
              <Button size="sm" variant="primary" onClick={() => s.setExportOpen(tracked.photoIds ?? outputs.map((o) => o.photo_id))}>
                {t('oven.export')}
              </Button>
            )}
          </div>
          <p className="field-help">{t('oven.oneAtATime')}</p>
        </div>

        {isActive && job?.stage && (
          <div className="oven-now">
            <h2 className="heading">{t('oven.now')}</h2>
            <p>
              <span className="spinner" aria-hidden="true" /> {job.stage}
            </p>
          </div>
        )}

        {job?.errors && job.errors.length > 0 && (
          <InlineAlert tone="error">
            <ul className="plain">
              {job.errors.map((e, i) => (
                <li key={i}>
                  <strong>{e.photo_id ? nameOf(e.photo_id) : ''}</strong> {e.message} {e.code ? <span className="muted mono">({e.code})</span> : null}
                </li>
              ))}
            </ul>
          </InlineAlert>
        )}

        <h2 className="heading">{t(isExport ? 'oven.finished.export' : 'oven.finished', { n: finishedCount })}</h2>
        {outputs.length === 0 ? (
          <p className="muted">{isActive ? tt('Las fotos aparecerán aquí a medida que terminen.', 'Photos appear here as they finish.') : '—'}</p>
        ) : (
          <div className="oven-grid">
            {outputs.map((o, i) => {
              const web = api.assetUrl(o.web_url || o.url);
              return (
                <figure key={`${o.photo_id}-${i}`} className="oven-cell">
                  {web ? (
                    <button type="button" className="oven-thumb" onClick={() => setPreview(web)} aria-label={`${t('oven.openResult')}: ${nameOf(o.photo_id)}`}>
                      <img src={web} alt="" loading="lazy" />
                      <span className="oven-ok" aria-hidden="true">
                        ✓
                      </span>
                    </button>
                  ) : (
                    <div className="oven-thumb file">✓ {o.path?.split('/').pop()}</div>
                  )}
                  <figcaption className="mono small ellipsis" title={o.path ?? nameOf(o.photo_id)}>
                    {o.path ? o.path.split('/').pop() : nameOf(o.photo_id)}
                  </figcaption>
                </figure>
              );
            })}
          </div>
        )}

        <details className="log-drawer" open={logOpen} onToggle={(e) => setLogOpen((e.target as HTMLDetailsElement).open)}>
          <summary>
            {t('oven.log')} ({job?.log?.length ?? 0})
          </summary>
          <pre className="log mono" role="log" aria-live="off">
            {(job?.log ?? []).slice(-300).join('\n') || '—'}
          </pre>
        </details>
      </section>

      <aside className="panel panel-right" aria-label={t('oven.history')}>
        <div className="panel-heading">
          <h3>{t('oven.history')}</h3>
        </div>
        <ul className="job-list">
          {jobs.map((j) => {
            const st = s.jobStatus[j.id];
            const state = st === null ? 'lost' : st?.state ?? 'queued';
            return (
              <li key={j.id}>
                <button type="button" className={`job-item${j.id === currentId ? ' is-selected' : ''}`} onClick={() => setSelected(j.id)} aria-current={j.id === currentId || undefined}>
                  <span className="job-kind">{t(kindKey(String(j.kind)) as 'oven.kind.batch')}</span>
                  <span className="job-label ellipsis">{j.label}</span>
                  <span className={`job-state st-${state}`}>
                    {state === 'running' ? `${Math.round((st?.progress ?? 0) * 100)} %` : t(`oven.state.${state === 'cancelled' ? 'error' : state}` as 'oven.state.done')}
                    {state === 'cancelled' ? ` (${tt('detenido', 'stopped')})` : ''}
                  </span>
                  <span className="muted small">{new Date(j.createdAt).toLocaleString(s.prefs.lang === 'es' ? 'es-ES' : 'en-GB', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </aside>

      {confirmStop && (
        <Dialog
          title={t('oven.stop')}
          onClose={() => setConfirmStop(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmStop(false)}>
                {tt('Seguir revelando', 'Keep developing')}
              </Button>
              <Button variant="danger" onClick={stop}>
                {tt('Detener', 'Stop')}
              </Button>
            </>
          }
        >
          <p>{tt('¿Detener el lote? Se para entre fotos: la foto en curso termina y las terminadas se conservan.', 'Stop the batch? It stops between photos: the current photo finishes and finished photos are kept.')}</p>
        </Dialog>
      )}
      {preview && (
        <Dialog title={t('oven.openResult')} onClose={() => setPreview(null)} wide>
          <img className="oven-preview" src={preview} alt="" />
          <p>
            <a href={preview} target="_blank" rel="noreferrer">
              {tt('Abrir en otra pestaña ↗', 'Open in a new tab ↗')}
            </a>
          </p>
        </Dialog>
      )}
    </div>
  );
}
