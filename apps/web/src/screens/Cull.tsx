// SPDX-License-Identifier: GPL-3.0-or-later
// Cull / Selección: fast keyboard culling, a free local
// technical pass, and Claude as an optional assistant that proposes and never
// silently overrides the user's own flags.
import { useEffect, useMemo, useState } from 'react';
import { api, type CullEstimate, type Photo } from '../api';
import { GridSkeleton, ThumbGrid, verdictLabel } from '../components/ThumbGrid';
import { canDevelop } from '../capabilities';
import { Badge, Button, Dialog, EmptyState, FlagToggles, InlineAlert, PairBadge, ProgressBar, RatingStars, Section, Segmented, SoonButton } from '../components/ui';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore } from '../store';

type Show = 'all' | 'pick' | 'none' | 'reject';

function proposal(p: Photo): 'pick' | 'reject' | null | undefined {
  const v = p.cull?.claude?.verdict ?? p.cull?.technical?.verdict;
  if (!v) return undefined;
  if (v === 'hero' || v === 'keep') return 'pick';
  if (v === 'reject') return 'reject';
  return null;
}

function ClaudeDialog({ onClose }: { onClose: () => void }) {
  const s = useStore();
  const { t, tt, num } = useI18n();
  const [scope, setScope] = useState<'all' | 'none'>('all');
  const [est, setEst] = useState<CullEstimate | null>(null);
  const [estErr, setEstErr] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const ids = useMemo(() => (scope === 'all' ? s.photos : s.photos.filter((p) => !p.flag)).map((p) => p.id), [scope, s.photos]);

  useEffect(() => {
    if (!s.session) return;
    setEst(null);
    setEstErr(null);
    api
      .cullEstimate(s.session.session_id, scope === 'all' ? undefined : ids)
      .then(setEst)
      .catch((e) => setEstErr(s.errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, s.session?.session_id]);

  const start = async () => {
    if (!s.session) return;
    setBusy(true);
    try {
      const { job_id } = await api.cullAuto(s.session.session_id, 'claude', { photo_ids: scope === 'all' ? undefined : ids, allow_cloud: true });
      s.trackJob({ id: job_id, kind: 'cull_claude', label: t('cull.claude.cta'), createdAt: Date.now() });
      onClose();
    } catch (e) {
      s.errorToast(e, start);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={t('cull.claude.cta')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" onClick={start} disabled={!consent || !ids.length} loading={busy} why={!consent ? tt('Acepta el envío de copias reducidas para continuar.', 'Accept sending downscaled copies to continue.') : undefined}>
            {t('cull.claude.start')}
          </Button>
        </>
      }
    >
      <div className="form-grid">
        <div className="field">
          <span className="heading">{tt('¿Qué fotos revisar?', 'Which photos to review?')}</span>
          <Segmented
            label={tt('Fotos', 'Photos')}
            value={scope}
            onChange={setScope}
            options={[
              { value: 'all', label: tt('Todas ({n})', 'All ({n})', { n: s.photos.length }) },
              { value: 'none', label: tt('Sin marcar ({n})', 'Unflagged ({n})', { n: s.photos.filter((p) => !p.flag).length }) },
            ]}
          />
        </div>
        <div className="field">
          <span className="heading">{tt('Coste estimado', 'Estimated cost')}</span>
          {est ? (
            <p>
              ≈ <strong>${num(est.est_usd, 2)}</strong> · {est.frames} {tt('fotos', 'photos')} · {num(est.est_input_tokens)} {tt('tokens de entrada', 'input tokens')} ·{' '}
              {num(est.est_output_tokens)} {tt('de salida', 'output')} <span className="muted">(±50 %)</span>
            </p>
          ) : estErr ? (
            <InlineAlert tone="warn">{estErr}</InlineAlert>
          ) : (
            <p className="muted">{t('common.loading')}</p>
          )}
        </div>
        <p className="field-help">
          {tt(
            'Primero una pasada técnica local (gratis). Luego Claude revisa copias de ≤ 512 px y las finalistas a ≤ 1024 px, sin EXIF, sin nombres ni rutas. Nada cambia hasta que aceptes la propuesta.',
            'First a local technical pass (free). Then Claude reviews ≤ 512 px copies and finalists at ≤ 1024 px, without EXIF, names or paths. Nothing changes until you accept the proposal.',
          )}
        </p>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          {tt('Acepto enviar estas copias reducidas a Anthropic para esta selección.', 'I agree to send these downscaled copies to Anthropic for this cull.')}
        </label>
      </div>
    </Dialog>
  );
}

export function Cull() {
  const s = useStore();
  const { t, tt, lang } = useI18n();
  const [show, setShow] = useState<Show>('all');
  const [focus, setFocus] = useState(0);
  const [claudeOpen, setClaudeOpen] = useState(false);
  const [starting, setStarting] = useState(false);

  const ordered = useMemo(
    () => [...s.photos].sort((a, b) => String(a.datetime ?? '').localeCompare(String(b.datetime ?? '')) || a.name.localeCompare(b.name)),
    [s.photos],
  );
  const counts = useMemo(
    () => ({
      all: ordered.length,
      pick: ordered.filter((p) => p.flag === 'pick').length,
      none: ordered.filter((p) => !p.flag).length,
      reject: ordered.filter((p) => p.flag === 'reject').length,
    }),
    [ordered],
  );
  const list = useMemo(() => (show === 'all' ? ordered : ordered.filter((p) => (show === 'none' ? !p.flag : p.flag === show))), [ordered, show]);
  useEffect(() => {
    if (focus >= list.length) setFocus(Math.max(0, list.length - 1));
  }, [list.length, focus]);
  const current = list[focus];

  const cullJob = s.activeJobs.find((j) => typeof j.kind === 'string' && j.kind.startsWith('cull'));
  const cullStatus = cullJob ? s.jobStatus[cullJob.id] : null;

  const pending = useMemo(() => ordered.filter((p) => !p.flag && proposal(p) !== undefined && proposal(p) !== null), [ordered]);
  const picks = ordered.filter((p) => p.flag === 'pick');
  const picksBlocked = picks.filter((p) => !canDevelop(p, s.caps));

  const runTechnical = async () => {
    if (!s.session) return;
    setStarting(true);
    try {
      const { job_id } = await api.cullAuto(s.session.session_id, 'technical');
      s.trackJob({ id: job_id, kind: 'cull_technical', label: t('cull.technical'), createdAt: Date.now() });
    } catch (e) {
      s.errorToast(e, runTechnical);
    } finally {
      setStarting(false);
    }
  };

  const acceptAll = () => {
    pending.forEach((p) => {
      const f = proposal(p);
      if (f) s.rate(p.id, { flag: f });
    });
    s.toast({ kind: 'success', text: tt('Propuesta aceptada en {n} fotos.', 'Proposal accepted for {n} photos.', { n: pending.length }) });
  };

  const sendToDevelop = () => {
    // Send every pick: Develop lists the ones it can't develop and says why.
    const ids = picks.map((p) => p.id);
    const ok = picks.filter((p) => canDevelop(p, s.caps)).map((p) => p.id);
    s.setUi({ developIds: ids, sampleId: s.ui.sampleId && ok.includes(s.ui.sampleId) ? s.ui.sampleId : ok[0] ?? null });
    navigate('develop');
  };

  if (!s.session) {
    return <EmptyState icon="▢" title={t('lib.empty.title')} body={t('lib.empty.body')} action={<Button variant="primary" onClick={() => navigate('library')}>{t('nav.library')}</Button>} />;
  }

  const claudeWhy = !s.aiProvider ? t('cull.claude.noKey') : undefined;
  const claude = current?.cull?.claude;
  const tech = current?.cull?.technical;

  return (
    <div className="workspace no-left">
      <section className="stage" aria-label={t('cull.title')}>
        <div className="toolbar" role="toolbar" aria-label={t('cull.title')}>
          <h1 className="title">{t('cull.title')}</h1>
          <Segmented
            size="sm"
            label={t('cull.show')}
            value={show}
            onChange={setShow}
            options={[
              { value: 'all', label: `${t('lib.flag.all')} ${counts.all}` },
              { value: 'pick', label: `${t('lib.flag.pick')} ${counts.pick}` },
              { value: 'none', label: `${t('lib.flag.none')} ${counts.none}` },
              { value: 'reject', label: `${t('lib.flag.reject')} ${counts.reject}` },
            ]}
          />
          <span className="grow" />
          <span className="num">{t('cull.count', { n: counts.pick, target: s.prefs.cullTarget })}</span>
          <label className="field-inline">
            <span>{t('cull.target')}</span>
            <input
              className="input sm num"
              type="number"
              min={1}
              max={5000}
              style={{ width: 72 }}
              value={s.prefs.cullTarget}
              onChange={(e) => s.setPrefs({ cullTarget: Math.max(1, Number(e.target.value) || 1) })}
            />
          </label>
          <label className="check" title={t('cull.autoAdvance')}>
            <input type="checkbox" checked={s.prefs.autoAdvance} onChange={(e) => s.setPrefs({ autoAdvance: e.target.checked })} />
            {t('cull.autoAdvance')}
          </label>
        </div>
        <div className="toolbar toolbar-2" role="toolbar" aria-label={tt('Acciones de selección', 'Cull actions')}>
          <Button size="sm" onClick={runTechnical} loading={starting} disabled={!!cullStatus || !s.photos.length} why={cullStatus ? tt('Ya hay una selección automática en curso.', 'An automatic cull is already running.') : undefined} title={t('cull.technical.tip')}>
            ◎ {t('cull.technical')}
          </Button>
          <Button size="sm" onClick={() => setClaudeOpen(true)} disabled={!s.aiProvider || !!cullStatus} why={claudeWhy}>
            ✨ {t('cull.claude.cta')}
          </Button>
          {!s.aiProvider && (
            <a className="small-link" href="#/settings">
              {tt('Configurar', 'Set up')}
            </a>
          )}
          <SoonButton size="sm" label={t('cull.compare')} why={t('cull.compare.soon')} />
          {pending.length > 0 && (
            <Button size="sm" onClick={acceptAll}>
              {tt('Aceptar propuesta ({n})', 'Accept proposal ({n})', { n: pending.length })}
            </Button>
          )}
          <span className="grow" />
          <Button size="sm" variant="primary" onClick={sendToDevelop} disabled={!picks.length || picks.length === picksBlocked.length} why={picks.length ? t('dev.needsRaw') : tt('Elige al menos una foto (P).', 'Pick at least one photo (P).')}>
            {t('cull.send', { n: picks.length - picksBlocked.length })}
          </Button>
        </div>
        {cullStatus && (
          <div className="job-strip">
            <span>{t('cull.running', { p: Math.round((cullStatus.progress ?? 0) * 100) })}</span>
            {cullStatus.stage && <span className="muted">· {cullStatus.stage}</span>}
            <ProgressBar value={cullStatus.progress ?? 0} label={t('cull.title')} valueText={`${Math.round((cullStatus.progress ?? 0) * 100)} %`} indeterminate={cullStatus.state === 'queued'} />
            {cullJob && (
              <Button size="sm" variant="ghost" onClick={() => api.cancelJob(cullJob.id).then(() => s.refreshJob(cullJob.id)).catch((e) => s.errorToast(e))}>
                {t('common.cancel')}
              </Button>
            )}
          </div>
        )}
        {picksBlocked.length > 0 && <InlineAlert tone="warn">{t('cull.sendNoRaw', { n: picksBlocked.length })} {t('dev.needsRaw')}.</InlineAlert>}
        <div className="stage-scroll">
          {s.photosState === 'loading' && !s.photos.length ? (
            <GridSkeleton size={s.prefs.thumbSize} />
          ) : !list.length ? (
            <EmptyState icon="⚑" title={show === 'pick' ? t('cull.empty') : t('lib.filtered.title')} action={show !== 'all' ? <Button onClick={() => setShow('all')}>{t('lib.flag.all')}</Button> : undefined} />
          ) : (
            <ThumbGrid
              photos={list}
              size={s.prefs.thumbSize}
              focusIndex={focus}
              setFocusIndex={setFocus}
              onOpen={(p) => navigate(`viewer/${encodeURIComponent(p.id)}`)}
              onRate={(p, patch) => s.rate(p.id, patch)}
              autoAdvance={s.prefs.autoAdvance}
              showCull
              label={t('cull.title')}
            />
          )}
        </div>
        <div className="stage-foot muted">{t('cull.keys')}</div>
      </section>

      <aside className="panel panel-right" aria-label={tt('Foto actual', 'Current photo')}>
        {current ? (
          <>
            <div className="cull-preview">
              <img src={api.thumbUrl(current.id, 800)} alt={current.name} />
            </div>
            <Section title={current.name} aside={<PairBadge photo={current} />}>
              <RatingStars value={current.rating} onChange={(n) => s.rate(current.id, { rating: n })} />
              <FlagToggles value={current.flag} onChange={(f) => s.rate(current.id, { flag: f })} />
              <p className="muted small">
                {[current.camera, current.lens].filter(Boolean).join(' · ')}
                {current.iso ? ` · ISO ${current.iso}` : ''}
                {current.shutter ? ` · ${current.shutter}` : ''}
                {current.aperture ? ` · f/${current.aperture}` : ''}
              </p>
            </Section>
            {claude?.verdict && (
              <Section title={`Claude · ${verdictLabel(claude.verdict, lang)}${claude.score != null ? ` · ${claude.score}` : ''}`}>
                <ul className="reasons">{(claude.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
                {claude.confidence && (
                  <p className="muted">
                    {tt('Confianza', 'Confidence')}: {claude.confidence}
                  </p>
                )}
              </Section>
            )}
            {tech && (
              <Section title={`${tt('Técnica', 'Technical')} · ${verdictLabel(tech.verdict, lang)}${tech.score != null ? ` · ${Math.round(tech.score)}` : ''}`}>
                {tech.burst_best && <Badge tone="accent">★ {tt('Mejor de la ráfaga', 'Best of burst')}</Badge>}
                {tech.burst_size && tech.burst_size > 1 && (
                  <p className="muted">
                    {tt('Ráfaga de {n} fotos', 'Burst of {n} photos', { n: tech.burst_size })}
                  </p>
                )}
                <ul className="reasons">{(tech.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
                <dl className="kv-list">
                  {tech.sharpness_af != null && <div className="kv"><dt>{tt('Nitidez AF', 'AF sharpness')}</dt><dd>{Number(tech.sharpness_af).toFixed(2)}</dd></div>}
                  {tech.sharpness_face != null && <div className="kv"><dt>{tt('Nitidez cara', 'Face sharpness')}</dt><dd>{Number(tech.sharpness_face).toFixed(2)}</dd></div>}
                  {tech.faces != null && <div className="kv"><dt>{tt('Caras', 'Faces')}</dt><dd>{tech.faces}</dd></div>}
                  {tech.eyes_open != null && <div className="kv"><dt>{tt('Ojos abiertos', 'Eyes open')}</dt><dd>{tech.eyes_open ? '✓' : '✗'}</dd></div>}
                  {tech.exposure?.verdict && <div className="kv"><dt>{tt('Exposición', 'Exposure')}</dt><dd>{tech.exposure.verdict}</dd></div>}
                </dl>
              </Section>
            )}
            {proposal(current) !== undefined && !current.flag && (
              <div className="row pad">
                <Button size="sm" onClick={() => proposal(current) && s.rate(current.id, { flag: proposal(current) ?? null })} disabled={!proposal(current)}>
                  {tt('Aceptar', 'Accept')}
                </Button>
              </div>
            )}
            <div className="row pad">
              <Button size="sm" variant="ghost" onClick={() => navigate(`viewer/${encodeURIComponent(current.id)}`)}>
                {tt('Abrir en el visor', 'Open in viewer')} (Enter)
              </Button>
            </div>
          </>
        ) : (
          <p className="muted pad">{t('cull.empty')}</p>
        )}
      </aside>
      {claudeOpen && <ClaudeDialog onClose={() => setClaudeOpen(false)} />}
    </div>
  );
}
