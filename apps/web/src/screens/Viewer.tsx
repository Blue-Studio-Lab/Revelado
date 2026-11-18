// SPDX-License-Identifier: GPL-3.0-or-later
// Viewer: fit / 100 % (real 1:1 via /crop), diagnostic masks
// with legends and measured stats, EXIF, camera + lens profile, cull reasons,
// and the developed outputs of this photo.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type MaskKind, type Photo, type PhotoOutput, type Profile } from '../api';
import { hasJpg } from '../capabilities';
import { verdictLabel } from '../components/ThumbGrid';
import { Badge, Button, EmptyState, FlagToggles, InlineAlert, KV, PairBadge, RatingStars, Section, Segmented, Skeleton, isTyping } from '../components/ui';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore } from '../store';

type MaskSel = 'none' | MaskKind;
const MASKS: MaskSel[] = ['none', 'noise', 'ca', 'shading'];

function fmtVal(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  if (typeof v === 'boolean') return v ? '✓' : '✗';
  if (Array.isArray(v)) return v.map(fmtVal).join(', ');
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${fmtVal(x)}`).join(' · ');
  return String(v);
}

function ObjList({ obj }: { obj: Record<string, unknown> | string | null | undefined }) {
  const { tt } = useI18n();
  if (obj == null) return <p className="muted">{tt('Sin datos', 'No data')}</p>;
  if (typeof obj === 'string') return <p>{obj}</p>;
  const entries = Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!entries.length) return <p className="muted">{tt('Sin datos', 'No data')}</p>;
  return (
    <dl className="kv-list">
      {entries.map(([k, v]) => (
        <KV key={k} k={k.replace(/_/g, ' ')} v={fmtVal(v)} />
      ))}
    </dl>
  );
}

function MaskLegend({ kind, stats, opacity, setOpacity }: { kind: MaskKind; stats: Record<string, unknown> | null; opacity: number; setOpacity: (n: number) => void }) {
  const { t } = useI18n();
  const conf = {
    noise: { grad: 'var(--mask-cividis)', lo: t('view.mask.noise.lo'), hi: t('view.mask.noise.hi'), note: t('view.mask.noise.note'), title: t('view.mask.noise') },
    ca: { grad: 'var(--mask-viridis)', lo: t('view.mask.ca.lo'), hi: t('view.mask.ca.hi'), note: t('view.mask.ca.note'), title: t('view.mask.ca') },
    shading: { grad: 'var(--mask-diverging)', lo: t('view.mask.shading.lo'), hi: t('view.mask.shading.hi'), note: t('view.mask.shading.note'), title: t('view.mask.shading') },
  }[kind];
  return (
    <div className="mask-legend" role="group" aria-label={`${t('view.mask.legend')}: ${conf.title}`}>
      <div className="mask-legend-title">
        {t('view.mask.legend')}: {conf.title}
      </div>
      <div className="mask-ramp" style={{ background: conf.grad }} aria-hidden="true" />
      <div className="mask-ends">
        <span>{conf.lo}</span>
        <span>{conf.hi}</span>
      </div>
      <label className="field-inline">
        <span>{t('view.mask.opacity')}</span>
        <input type="range" min={0} max={100} value={Math.round(opacity * 100)} onChange={(e) => setOpacity(Number(e.target.value) / 100)} aria-valuetext={`${Math.round(opacity * 100)} %`} />
        <span className="num">{Math.round(opacity * 100)} %</span>
      </label>
      <p className="field-help">{conf.note}</p>
      {stats && (
        <details open>
          <summary>{t('view.mask.stats')}</summary>
          <ObjList obj={stats} />
        </details>
      )}
    </div>
  );
}

export function Viewer({ id }: { id: string | null }) {
  const s = useStore();
  const { t, tt, lang } = useI18n();
  const ordered = useMemo(
    () => [...s.photos].sort((a, b) => String(a.datetime ?? '').localeCompare(String(b.datetime ?? '')) || a.name.localeCompare(b.name)),
    [s.photos],
  );
  const idx = ordered.findIndex((p) => p.id === id);
  const photo: Photo | undefined = idx >= 0 ? ordered[idx] : undefined;

  const [zoom, setZoom] = useState<'fit' | '100'>('fit');
  const [mask, setMask] = useState<MaskSel>('none');
  const [opacity, setOpacity] = useState(0.85);
  const [infoOpen, setInfoOpen] = useState(true);
  const [previewState, setPreviewState] = useState<'loading' | 'ok' | 'error'>('loading');
  const [previewRetry, setPreviewRetry] = useState(0);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [maskData, setMaskData] = useState<{ url: string; stats: Record<string, unknown> | null } | null>(null);
  const [maskState, setMaskState] = useState<'idle' | 'loading' | 'ok' | 'error'>('idle');
  const [maskErr, setMaskErr] = useState('');
  const [exif, setExif] = useState<Record<string, unknown> | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileErr, setProfileErr] = useState(false);
  const [outputs, setOutputs] = useState<PhotoOutput[] | null>(null);
  const [center, setCenter] = useState<{ x: number; y: number }>({ x: 0.5, y: 0.5 }); // normalised
  const [cropState, setCropState] = useState<'loading' | 'ok' | 'error'>('loading');
  const stageRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ w: 800, h: 600 });
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setStageSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // reset per photo
  useEffect(() => {
    setPreviewState('loading');
    setNatural(null);
    setExif(null);
    setProfile(null);
    setProfileErr(false);
    setOutputs(null);
    setCenter({ x: 0.5, y: 0.5 });
    if (!photo) return;
    let alive = true;
    api.exif(photo.id).then((e) => alive && setExif(e)).catch(() => alive && setExif({}));
    api
      .profile(photo.id)
      .then((p) => alive && setProfile(p))
      .catch(() => alive && setProfileErr(true));
    api
      .outputs(photo.id)
      .then((o) => alive && setOutputs(o))
      .catch(() => alive && setOutputs([]));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photo?.id]);

  // masks
  useEffect(() => {
    setMaskData((old) => {
      if (old) URL.revokeObjectURL(old.url);
      return null;
    });
    if (!photo || mask === 'none') {
      setMaskState('idle');
      return;
    }
    if (!photo.has_raw) {
      setMaskState('error');
      setMaskErr(t('view.mask.jpgOnly'));
      return;
    }
    const ctrl = new AbortController();
    setMaskState('loading');
    api
      .mask(photo.id, mask, 1600, ctrl.signal)
      .then((d) => {
        setMaskData(d);
        setMaskState('ok');
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setMaskState('error');
        setMaskErr(s.errText(e));
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photo?.id, mask]);

  // full-res oriented dimensions: trust EXIF size but follow the preview's aspect
  const full = useMemo(() => {
    let w = photo?.width ?? 0;
    let h = photo?.height ?? 0;
    if (natural && w && h && natural.w > natural.h !== w > h) [w, h] = [h, w];
    if (!w || !h) return natural ? { w: natural.w, h: natural.h } : null;
    return { w, h };
  }, [photo?.width, photo?.height, natural]);

  const dpr = window.devicePixelRatio || 1;
  const crop = useMemo(() => {
    if (!full) return null;
    const w = Math.min(2048, Math.round(stageSize.w * dpr), full.w);
    const h = Math.min(2048, Math.round(stageSize.h * dpr), full.h);
    const x = Math.max(0, Math.min(full.w - w, center.x * full.w - w / 2));
    const y = Math.max(0, Math.min(full.h - h, center.y * full.h - h / 2));
    return { x, y, w, h };
  }, [full, stageSize, dpr, center]);

  useEffect(() => {
    if (zoom === '100') setCropState('loading');
  }, [zoom, crop?.x, crop?.y, crop?.w, crop?.h, photo?.id]);

  const go = useCallback(
    (d: number) => {
      const n = ordered[idx + d];
      if (n) navigate(`viewer/${encodeURIComponent(n.id)}`);
    },
    [ordered, idx],
  );

  // keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || s.shortcutsOpen || s.exportOpen) return;
      if (!photo) return;
      const k = e.key;
      if (e.altKey && /^Digit[1-3]$/.test(e.code)) {
        setMask(MASKS[Number(e.code.slice(5))]);
      } else if (e.altKey) return;
      else if (k === 'ArrowRight') go(1);
      else if (k === 'ArrowLeft') go(-1);
      else if (k === 'Escape') navigate('library');
      else if (k === 'z' || k === 'Z') setZoom((z) => (z === 'fit' ? '100' : 'fit'));
      else if (k === 'm' || k === 'M') setMask((m) => MASKS[(MASKS.indexOf(m) + 1) % MASKS.length]);
      else if (k === '[') setOpacity((o) => Math.max(0, o - 0.1));
      else if (k === ']') setOpacity((o) => Math.min(1, o + 0.1));
      else if (k === 'i' || k === 'I') setInfoOpen((v) => !v);
      else if (/^[0-5]$/.test(k)) s.rate(photo.id, { rating: Number(k) });
      else if (k === 'p' || k === 'P') s.rate(photo.id, { flag: 'pick' });
      else if (k === 'x' || k === 'X') s.rate(photo.id, { flag: 'reject' });
      else if (k === 'u' || k === 'U') s.rate(photo.id, { flag: null });
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [photo, go, s]);

  if (!photo) {
    if (s.photosState === 'loading' || s.openState === 'loading')
      return (
        <div className="viewer">
          <div className="viewer-stage">
            <Skeleton w="60%" h="60%" />
          </div>
        </div>
      );
    return <EmptyState icon="⊘" title={t('view.notFound')} action={<Button onClick={() => navigate('library')}>← {t('nav.library')}</Button>} />;
  }

  // click in fit → 100 % at that point
  const onFitClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setCenter({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
    setZoom('100');
  };

  const onDown = (e: React.PointerEvent) => {
    if (zoom !== '100' || !full) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, cx: center.x, cy: center.y, moved: false };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    setDragOffset({ x: dx, y: dy });
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    setDragOffset({ x: 0, y: 0 });
    if (!d || !full) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved) return;
    setCenter({
      x: Math.max(0, Math.min(1, d.cx - (dx * dpr) / full.w)),
      y: Math.max(0, Math.min(1, d.cy - (dy * dpr) / full.h)),
    });
  };

  const tech = photo.cull?.technical;
  const claude = photo.cull?.claude;
  const exifKeys = exif ? Object.keys(exif) : [];

  return (
    <div className={`viewer${infoOpen ? '' : ' no-info'}`}>
      <div className="toolbar viewer-toolbar" role="toolbar" aria-label={tt('Visor', 'Viewer')}>
        <Button size="sm" variant="ghost" onClick={() => navigate('library')}>
          ◂ {t('common.back')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => go(-1)} disabled={idx <= 0} aria-label={t('view.prev')}>
          ←
        </Button>
        <Button size="sm" variant="ghost" onClick={() => go(1)} disabled={idx >= ordered.length - 1} aria-label={t('view.next')}>
          →
        </Button>
        <strong className="mono">{photo.name}</strong>
        <PairBadge photo={photo} />
        <span className="muted num">
          {idx + 1} / {ordered.length}
        </span>
        <span className="grow" />
        <Segmented
          size="sm"
          label="Zoom"
          value={zoom}
          onChange={(v) => setZoom(v)}
          options={[
            { value: 'fit', label: t('view.fit') },
            { value: '100', label: t('view.100'), disabled: !full, title: t('view.100help') },
          ]}
        />
        <Segmented
          size="sm"
          label={t('view.mask')}
          value={mask}
          onChange={setMask}
          options={[
            { value: 'none', label: t('view.mask.none') },
            { value: 'noise', label: t('view.mask.noise') },
            { value: 'ca', label: t('view.mask.caShort') },
            { value: 'shading', label: t('view.mask.shadingShort') },
          ]}
        />
        <Button size="sm" variant="ghost" aria-pressed={infoOpen} onClick={() => setInfoOpen((v) => !v)}>
          {t('view.info')} (I)
        </Button>
      </div>

      <div className="viewer-body">
        <div className="viewer-stage" ref={stageRef} id="photo">
          {/* where the pixels come from: the camera JPG, or (no camera JPG) a preview made from the RAW, as in Develop */}
          <span className="viewer-chip" data-testid="viewer-source" data-source={hasJpg(photo) ? 'camera_jpg' : 'raw'}>
            {hasJpg(photo) ? t('view.source.jpg') : t('view.source.raw')}
            {photo.is_log ? ` · ${tt('S-Log3 → Rec.709 solo para ver (no es un look)', 'S-Log3 → Rec.709 for viewing only (not a look)')}` : ''}
          </span>
          {zoom === 'fit' && (
            <div className="fit-wrap">
              {previewState === 'loading' && <Skeleton className="fit-skel" />}
              {previewState === 'error' ? (
                <EmptyState
                  icon="⊘"
                  title={t('thumb.error')}
                  action={
                    <Button
                      onClick={() => {
                        setPreviewState('loading');
                        setPreviewRetry((r) => r + 1);
                      }}
                    >
                      {t('common.retry')}
                    </Button>
                  }
                />
              ) : (
                <div className="fit-layer">
                  <img
                    key={`${photo.id}-${previewRetry}`}
                    className={`fit-img${previewState === 'ok' ? ' loaded' : ''}`}
                    src={api.previewUrl(photo.id, 2048) + (previewRetry ? `&r=${previewRetry}` : '')}
                    alt={photo.name}
                    onLoad={(e) => {
                      setPreviewState('ok');
                      setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight });
                    }}
                    onError={() => setPreviewState('error')}
                    onClick={onFitClick}
                    title={t('view.100help')}
                    draggable={false}
                  />
                  {maskData && mask !== 'none' && <img className="mask-img" src={maskData.url} alt="" style={{ opacity }} aria-hidden="true" />}
                </div>
              )}
            </div>
          )}
          {zoom === '100' && crop && (
            <div className="crop-wrap" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
              <img
                key={`${crop.x}-${crop.y}-${crop.w}-${crop.h}`}
                className={`crop-img${cropState === 'ok' ? ' loaded' : ''}`}
                src={api.cropUrl(photo.id, crop.x, crop.y, crop.w, crop.h)}
                alt={`${photo.name} 100 %`}
                style={{ width: crop.w / dpr, height: crop.h / dpr, transform: `translate(${dragOffset.x}px, ${dragOffset.y}px)` }}
                onLoad={() => setCropState('ok')}
                onError={() => setCropState('error')}
                draggable={false}
              />
              {cropState === 'loading' && <span className="loading-chip">{t('view.loading100')}…</span>}
              {cropState === 'error' && <span className="loading-chip danger">⊘ {t('thumb.error')}</span>}
              <span className="zoom-chip">
                {t('view.100chip')} · x {Math.round(crop.x + crop.w / 2)} y {Math.round(crop.y + crop.h / 2)}
              </span>
              {mask !== 'none' && <span className="loading-chip info">{tt('Las máscaras se ven en modo Encajar.', 'Masks are shown in Fit mode.')}</span>}
            </div>
          )}
          {mask !== 'none' && zoom === 'fit' && (
            <div className="viewer-legend">
              {maskState === 'loading' && <span className="loading-chip">{t('view.mask.computing')}</span>}
              {maskState === 'error' && <InlineAlert tone={photo.has_raw ? 'error' : 'info'}>{maskErr || t('view.mask.error')}</InlineAlert>}
              {maskState === 'ok' && <MaskLegend kind={mask as MaskKind} stats={maskData?.stats ?? null} opacity={opacity} setOpacity={setOpacity} />}
            </div>
          )}
        </div>

        {infoOpen && (
          <aside className="panel panel-right" aria-label={t('view.info')}>
            <Section title={t('rating.label')}>
              <RatingStars value={photo.rating} onChange={(n) => s.rate(photo.id, { rating: n })} />
              <FlagToggles value={photo.flag} onChange={(f) => s.rate(photo.id, { flag: f })} />
            </Section>
            <Section title={t('view.profile')}>
              <dl className="kv-list">
                <KV k={t('view.camera')} v={photo.camera} />
                <KV k={t('view.lens')} v={photo.lens} />
                <KV k="ISO" v={photo.iso} />
                <KV k={tt('Velocidad', 'Shutter')} v={photo.shutter} />
                <KV k={tt('Diafragma', 'Aperture')} v={photo.aperture != null ? `f/${photo.aperture}` : null} />
                <KV k={tt('Focal', 'Focal')} v={photo.focal != null ? `${photo.focal} mm` : null} />
                <KV k={tt('Tamaño', 'Size')} v={photo.width && photo.height ? `${photo.width} × ${photo.height}` : null} />
                {photo.picture_profile && <KV k={tt('Perfil de imagen', 'Picture profile')} v={photo.picture_profile} />}
              </dl>
              {profile === null && !profileErr && <Skeleton w="100%" h={60} />}
              {profileErr && <p className="muted">{t('view.noProfile')}</p>}
              {profile && (
                <>
                  <h4 className="subheading">{t('view.camera')}</h4>
                  <ObjList obj={profile.camera_profile} />
                  <h4 className="subheading">{t('view.lens')}</h4>
                  <ObjList obj={profile.lens_profile} />
                  <h4 className="subheading">{t('view.recommended')}</h4>
                  {profile.recommended ? (
                    <dl className="kv-list">
                      {Object.entries(profile.recommended).map(([k, v]) => (
                        <KV key={k} k={(['nr_level', 'ca', 'defringe', 'sharpen', 'exposure_hint', 'log_profile'].includes(k) ? t(`view.rec.${k}` as 'view.rec.ca') : k.replace(/_/g, ' '))} v={fmtVal(v)} />
                      ))}
                    </dl>
                  ) : (
                    <p className="muted">{t('view.noProfile')}</p>
                  )}
                </>
              )}
            </Section>
            {(tech || claude) && (
              <Section title={tt('Selección automática', 'Automatic cull')}>
                {claude?.verdict && (
                  <p>
                    <Badge tone="accent" icon="✨">
                      Claude · {verdictLabel(claude.verdict, lang)} {claude.score != null ? `· ${claude.score}` : ''}
                    </Badge>
                  </p>
                )}
                {claude?.reasons && (
                  <ul className="reasons">
                    {claude.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                )}
                {tech && (
                  <>
                    <p>
                      <Badge icon="◎">
                        {tt('Técnica', 'Technical')} · {verdictLabel(tech.verdict, lang)} {tech.score != null ? `· ${Math.round(tech.score)}` : ''}
                        {tech.burst_best ? ` · ${tt('mejor de la ráfaga', 'best of burst')}` : ''}
                      </Badge>
                    </p>
                    {tech.reasons && (
                      <ul className="reasons">
                        {tech.reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </Section>
            )}
            <Section title={tt('Revelados', 'Developed')}>
              {outputs === null ? (
                <Skeleton w="100%" h={40} />
              ) : outputs.length === 0 ? (
                <p className="muted">{photo.has_raw ? tt('Aún no revelada.', 'Not developed yet.') : t('pair.jpgTip')}</p>
              ) : (
                <ul className="output-list">
                  {outputs.map((o) => (
                    <li key={o.output_id}>
                      <a href={api.safeAssetUrl(o.web_url) ?? '#'} target="_blank" rel="noopener noreferrer">
                        <img src={api.safeAssetUrl(o.web_url) ?? ''} alt="" loading="lazy" />
                      </a>
                      <span className="muted">
                        {String(o.recipe?.film ?? '—')} · {o.created_at ? (typeof o.created_at === 'number' ? new Date(o.created_at * 1000).toISOString() : String(o.created_at)).slice(0, 16).replace('T', ' ') : ''}
                      </span>
                      <a href={api.safeAssetUrl(o.url) ?? '#'} target="_blank" rel="noopener noreferrer">
                        {tt('Completo ↗', 'Full ↗')}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <Section title={t('view.exif')}>
              {exif === null ? (
                <Skeleton w="100%" h={80} />
              ) : exifKeys.length ? (
                <details>
                  <summary>{tt('{n} etiquetas', '{n} tags', { n: exifKeys.length })}</summary>
                  <ObjList obj={exif} />
                </details>
              ) : (
                <p className="muted">{tt('Sin datos', 'No data')}</p>
              )}
            </Section>
          </aside>
        )}
      </div>
    </div>
  );
}
