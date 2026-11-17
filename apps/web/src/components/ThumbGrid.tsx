// SPDX-License-Identifier: GPL-3.0-or-later
// ThumbnailGrid + ThumbnailCell: roving tabindex,
// arrow keys move focus, 0–5 / P / X / U rate, Enter opens the viewer.
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, type Flag, type Photo } from '../api';
import { useI18n } from '../i18n';
import { PairBadge, isTyping } from './ui';
import { canDevelop, developability, pairOf, reasonText } from '../capabilities';
import { useStore } from '../store';

export function verdictLabel(v: string | undefined, lang: 'es' | 'en'): string {
  const es: Record<string, string> = { hero: 'Héroe', keep: 'Buena', maybe: 'Quizá', reject: 'Descartar' };
  const en: Record<string, string> = { hero: 'Hero', keep: 'Keep', maybe: 'Maybe', reject: 'Reject' };
  if (!v) return '';
  return (lang === 'es' ? es : en)[v] ?? v;
}

interface CellProps {
  photo: Photo;
  size: number;
  focused: boolean;
  tabbable: boolean;
  onFocusMe: () => void;
  onOpen: () => void;
  showCull: boolean;
}

const ThumbCell = memo(function ThumbCell({ photo, size, focused, tabbable, onFocusMe, onOpen, showCull }: CellProps) {
  const { t, lang } = useI18n();
  const { caps } = useStore();
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading');
  const [retry, setRetry] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focused && ref.current && document.activeElement !== ref.current && ref.current.closest('[data-grid-active="true"]')) {
      ref.current.focus({ preventScroll: false });
      ref.current.scrollIntoView({ block: 'nearest' });
    }
  }, [focused]);
  const tech = photo.cull?.technical;
  const claude = photo.cull?.claude;
  const name = [
    photo.name,
    photo.rating ? t('rating.stars', { n: photo.rating }) : '',
    photo.flag === 'pick' ? t('flag.pick') : photo.flag === 'reject' ? t('flag.reject') : '',
    pairOf(photo) === 'both' ? t('pair.both') : pairOf(photo) === 'raw' ? t('pair.raw') : t('pair.jpg'),
    canDevelop(photo, caps) ? '' : reasonText(developability(photo, caps).reason, t),
    photo.datetime ? String(photo.datetime).slice(11, 16) : '',
    claude?.verdict ? `Claude: ${verdictLabel(claude.verdict, lang)}${claude.score != null ? ', ' + claude.score : ''}` : '',
    tech?.verdict ? `${lang === 'es' ? 'Técnica' : 'Technical'}: ${verdictLabel(tech.verdict, lang)}${tech.score != null ? ', ' + Math.round(tech.score) : ''}` : '',
  ]
    .filter(Boolean)
    .join(', ');
  return (
    <div
      ref={ref}
      role="gridcell"
      aria-label={name}
      aria-selected={focused}
      tabIndex={tabbable ? 0 : -1}
      className={`thumb${photo.flag === 'reject' ? ' is-rejected' : ''}${focused ? ' is-current' : ''}`}
      style={{ width: size }}
      onFocus={onFocusMe}
      onClick={onFocusMe}
      onDoubleClick={onOpen}
    >
      <div className="thumb-img" style={{ height: Math.round(size * 0.72) }}>
        {state === 'loading' && <span className="skeleton thumb-skel" aria-hidden="true" />}
        {state === 'error' ? (
          <button
            type="button"
            className="thumb-error"
            onClick={(e) => {
              e.stopPropagation();
              setState('loading');
              setRetry((r) => r + 1);
            }}
          >
            ⊘ {t('thumb.error')} · {t('common.retry')}
          </button>
        ) : (
          <img
            key={retry}
            src={api.thumbUrl(photo.id, 400) + (retry ? `&r=${retry}` : '')}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            onLoad={() => setState('ok')}
            onError={() => setState('error')}
            className={state === 'ok' ? 'loaded' : ''}
          />
        )}
        <div className="thumb-top">
          {photo.flag === 'pick' && (
            <span className="thumb-flag pick" title={t('flag.pick')}>
              ⚑
            </span>
          )}
          {photo.flag === 'reject' && (
            <span className="thumb-flag reject" title={t('flag.reject')}>
              ✕
            </span>
          )}
          {!!photo.outputs && (
            <span className="thumb-dev" title={lang === 'es' ? `${photo.outputs} revelado(s)` : `${photo.outputs} developed`}>
              ◉
            </span>
          )}
        </div>
        <div className="thumb-bottom">
          <PairBadge photo={photo} />
          {showCull && claude?.verdict && <span className={`verdict-chip v-${claude.verdict}`}>✨ {verdictLabel(claude.verdict, lang)}</span>}
          {showCull && !claude?.verdict && tech?.verdict && (
            <span className={`verdict-chip v-${tech.verdict}`} title={(tech.reasons ?? []).join(' · ')}>
              ◎ {verdictLabel(tech.verdict, lang)}
              {tech.burst_best ? ' ★' : ''}
            </span>
          )}
        </div>
      </div>
      <div className="thumb-meta">
        <span className="thumb-name">{photo.name}</span>
        {photo.rating > 0 && <span className="thumb-stars">{'★'.repeat(photo.rating)}</span>}
      </div>
    </div>
  );
});

export interface ThumbGridProps {
  photos: Photo[];
  size: number;
  focusIndex: number;
  setFocusIndex: (i: number) => void;
  onOpen: (p: Photo) => void;
  onRate: (p: Photo, patch: { rating?: number; flag?: Flag }) => void;
  autoAdvance?: boolean;
  showCull?: boolean;
  label: string;
}

export function ThumbGrid({ photos, size, focusIndex, setFocusIndex, onOpen, onRate, autoAdvance, showCull = true, label }: ThumbGridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [cols, setCols] = useState(4);
  const [active, setActive] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setCols(Math.max(1, Math.floor((el.clientWidth - 16) / (size + 12))));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [size]);

  const move = useCallback(
    (i: number) => {
      if (!photos.length) return;
      setActive(true);
      setFocusIndex(Math.max(0, Math.min(photos.length - 1, i)));
    },
    [photos.length, setFocusIndex],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
    const p = photos[focusIndex];
    const k = e.key;
    const advance = () => autoAdvance && move(focusIndex + 1);
    if (k === 'ArrowRight') move(focusIndex + 1);
    else if (k === 'ArrowLeft') move(focusIndex - 1);
    else if (k === 'ArrowDown') move(focusIndex + cols);
    else if (k === 'ArrowUp') move(focusIndex - cols);
    else if (k === 'Home') move(0);
    else if (k === 'End') move(photos.length - 1);
    else if ((k === 'Enter' || k === 'e' || k === 'E') && p) onOpen(p);
    else if (/^[0-5]$/.test(k) && p) {
      onRate(p, { rating: Number(k) });
      advance();
    } else if ((k === 'p' || k === 'P') && p) {
      onRate(p, { flag: 'pick' });
      advance();
    } else if ((k === 'x' || k === 'X') && p) {
      onRate(p, { flag: 'reject' });
      advance();
    } else if ((k === 'u' || k === 'U') && p) onRate(p, { flag: null });
    else return;
    e.preventDefault();
  };

  return (
    <div
      ref={ref}
      className="thumb-grid"
      role="grid"
      aria-label={label}
      aria-rowcount={Math.ceil(photos.length / cols)}
      data-grid-active={active}
      onKeyDown={onKeyDown}
      onFocus={() => setActive(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setActive(false);
      }}
    >
      <div role="row" className="thumb-row">
        {photos.map((p, i) => (
          <ThumbCell
            key={p.id}
            photo={p}
            size={size}
            focused={i === focusIndex}
            tabbable={i === focusIndex}
            onFocusMe={() => {
              if (i !== focusIndex) setFocusIndex(i);
            }}
            onOpen={() => onOpen(p)}
            showCull={showCull}
          />
        ))}
      </div>
    </div>
  );
}

export function GridSkeleton({ size, n = 18 }: { size: number; n?: number }) {
  return (
    <div className="thumb-grid" aria-busy="true">
      <div className="thumb-row">
        {Array.from({ length: n }).map((_, i) => (
          <div key={i} className="thumb" style={{ width: size }}>
            <div className="thumb-img" style={{ height: Math.round(size * 0.72) }}>
              <span className="skeleton thumb-skel" />
            </div>
            <div className="thumb-meta">
              <span className="skeleton" style={{ width: '60%', height: 10 }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
