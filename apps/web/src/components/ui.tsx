// SPDX-License-Identifier: GPL-3.0-or-later
// Small building blocks from the component inventory.
import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import type { Flag, Photo } from '../api';
import { developability, pairOf, reasonText } from '../capabilities';
import { useI18n } from '../i18n';
import { useStore } from '../store';

// ── Button
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  /** Explains why the button is disabled (shown as tooltip and to screen readers). */
  why?: string;
};
export function Button({ variant = 'secondary', size = 'md', loading, why, className, children, disabled, ...rest }: BtnProps) {
  const descId = useId();
  return (
    <>
      <button
        type="button"
        className={`btn btn-${variant} btn-${size}${loading ? ' is-loading' : ''}${className ? ' ' + className : ''}`}
        aria-disabled={disabled || loading ? true : undefined}
        disabled={disabled}
        aria-busy={loading || undefined}
        title={disabled && why ? why : rest.title}
        aria-describedby={disabled && why ? descId : undefined}
        {...rest}
      >
        {loading && <span className="spinner" aria-hidden="true" />}
        {children}
      </button>
      {disabled && why && (
        <span id={descId} className="sr-only">
          {why}
        </span>
      )}
    </>
  );
}

// ── "Próximamente" marker
export function Soon({ why }: { why?: string }) {
  const { t } = useI18n();
  return (
    <span className="soon" title={why}>
      {t('common.soon')}
    </span>
  );
}

/** A visible but honest control for a feature that doesn't exist yet. */
export function SoonButton({ label, why, size = 'md' }: { label: string; why: string; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className="soon-btn">
      <Button size={size} disabled why={why}>
        {label}
      </Button>
      <Soon why={why} />
    </span>
  );
}

// ── Badge
export function Badge({ tone = 'neutral', icon, children, title }: { tone?: 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'accent'; icon?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {icon && (
        <span className="badge-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}

export function PairBadge({ photo }: { photo: Photo }) {
  const { t } = useI18n();
  const { caps } = useStore();
  const kind = pairOf(photo);
  const dev = developability(photo, caps);
  const why = dev.ok ? undefined : reasonText(dev.reason, t);
  const label = kind === 'both' ? t('pair.both') : kind === 'raw' ? t('pair.raw') : t('pair.jpg');
  return (
    <span className="pair-badge" data-pair={kind} data-developable={dev.ok ? 'yes' : 'no'}>
      <Badge tone={dev.ok ? 'neutral' : 'warn'} icon={kind === 'both' ? '◧' : kind === 'raw' ? '◨' : '▲'} title={why}>
        {label}
      </Badge>
      {why && <span className="sr-only"> — {why}</span>}
    </span>
  );
}

// ── Rating stars (radiogroup)
export function RatingStars({ value, onChange, size = 'md', readOnly }: { value: number; onChange?: (n: number) => void; size?: 'sm' | 'md'; readOnly?: boolean }) {
  const { t } = useI18n();
  if (readOnly || !onChange) {
    if (!value) return null;
    return (
      <span className={`stars stars-${size}`} aria-label={t('rating.stars', { n: value })}>
        {'★'.repeat(value)}
      </span>
    );
  }
  return (
    <div className={`stars stars-${size} stars-edit`} role="radiogroup" aria-label={t('rating.label')}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          role="radio"
          aria-checked={value === n}
          aria-label={t('rating.stars', { n })}
          className={n <= value ? 'on' : ''}
          onClick={() => onChange(value === n ? 0 : n)}
        >
          ★
        </button>
      ))}
    </div>
  );
}

export function FlagToggles({ value, onChange }: { value: Flag; onChange: (f: Flag) => void }) {
  const { t } = useI18n();
  return (
    <div className="flag-toggles">
      <button type="button" className="chip-btn" aria-pressed={value === 'pick'} onClick={() => onChange(value === 'pick' ? null : 'pick')}>
        ⚑ {t('flag.pick')} <kbd>P</kbd>
      </button>
      <button type="button" className="chip-btn" aria-pressed={value === 'reject'} onClick={() => onChange(value === 'reject' ? null : 'reject')}>
        ✕ {t('flag.reject')} <kbd>X</kbd>
      </button>
    </div>
  );
}

// ── Segmented control (radiogroup)
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = 'md',
}: {
  value: T;
  options: { value: T; label: string; disabled?: boolean; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: 'sm' | 'md';
}) {
  return (
    <div className={`segmented segmented-${size}`} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
          onKeyDown={(e) => {
            if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
            e.preventDefault();
            const enabled = options.filter((x) => !x.disabled);
            const i = enabled.findIndex((x) => x.value === value);
            const n = enabled[(i + (e.key === 'ArrowRight' ? 1 : enabled.length - 1)) % enabled.length];
            if (n) onChange(n.value);
            const parent = e.currentTarget.parentElement;
            requestAnimationFrame(() => (parent?.querySelector('[aria-checked="true"]') as HTMLElement | null)?.focus());
          }}
          tabIndex={value === o.value ? 0 : -1}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ── Empty state
export function EmptyState({ title, body, action, icon = '◌' }: { title: string; body?: ReactNode; action?: ReactNode; icon?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden="true">
        {icon}
      </div>
      <h2 className="empty-title">{title}</h2>
      {body && <p className="empty-body">{body}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

// ── Inline alert
export function InlineAlert({ tone = 'info', children, action, onDismiss }: { tone?: 'info' | 'warn' | 'error' | 'success'; children: ReactNode; action?: ReactNode; onDismiss?: () => void }) {
  const icon = { info: 'ⓘ', warn: '▲', error: '⊘', success: '✓' }[tone];
  return (
    <div className={`alert alert-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span className="alert-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="alert-body">{children}</div>
      {action && <div className="alert-action">{action}</div>}
      {onDismiss && (
        <button type="button" className="icon-btn" onClick={onDismiss} aria-label="×">
          ×
        </button>
      )}
    </div>
  );
}

// ── Skeleton
export function Skeleton({ w, h, className }: { w?: number | string; h?: number | string; className?: string }) {
  return <span className={`skeleton${className ? ' ' + className : ''}`} style={{ width: w, height: h }} aria-hidden="true" />;
}

// ── Progress bar
export function ProgressBar({ value, label, valueText, indeterminate }: { value: number; label: string; valueText?: string; indeterminate?: boolean }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={`progress${indeterminate ? ' is-indeterminate' : ''}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      aria-valuetext={valueText}
    >
      <div className="progress-fill" style={{ width: indeterminate ? undefined : `${pct}%` }} />
    </div>
  );
}

// ── Dialog (focus trap, Esc, returns focus)
export function Dialog({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = el?.querySelector<HTMLElement>('input, select, textarea, button:not([disabled]), [tabindex]:not([tabindex="-1"])');
    (first ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !el) return;
      const items = Array.from(
        el.querySelectorAll<HTMLElement>('a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
      ).filter((x) => x.offsetParent !== null);
      if (!items.length) return;
      const a = items[0];
      const b = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        b.focus();
      } else if (!e.shiftKey && document.activeElement === b) {
        e.preventDefault();
        a.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? ' dialog-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} tabIndex={-1}>
        <header className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Esc">
            ×
          </button>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-foot">{footer}</footer>}
      </div>
    </div>
  );
}

// ── Labelled panel section
export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="panel-section">
      <div className="panel-heading">
        <h3>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <dt>{k}</dt>
      <dd>{v ?? '—'}</dd>
    </div>
  );
}

export function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  return Promise.resolve(fallbackCopy(text));
}
function fallbackCopy(text: string): boolean {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

/** true when a keyboard event comes from a text field (single-key shortcuts must not fire). */
export function isTyping(e: KeyboardEvent | React.KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (el as HTMLInputElement).type;
    return !['range', 'checkbox', 'radio', 'button'].includes(type);
  }
  return el.isContentEditable;
}
