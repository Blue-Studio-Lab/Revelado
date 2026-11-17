// SPDX-License-Identifier: GPL-3.0-or-later
// Toasts: bottom-right, 6 s, paused on hover/focus. Errors carry a
// Retry action and stay longer (12 s) so there is time to act.
import { useEffect, useRef, useState } from 'react';
import { useI18n } from '../i18n';
import { useStore, type Toast } from '../store';

function ToastItem({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  const { t } = useI18n();
  const [paused, setPaused] = useState(false);
  const left = useRef(toast.kind === 'error' ? 12000 : 6000);
  useEffect(() => {
    if (paused) return;
    const start = Date.now();
    const timer = setTimeout(onDone, left.current);
    return () => {
      clearTimeout(timer);
      left.current -= Date.now() - start;
    };
  }, [paused, onDone]);
  const icon = { info: 'ⓘ', success: '✓', error: '⊘' }[toast.kind];
  return (
    <div
      className={`toast toast-${toast.kind}`}
      role={toast.kind === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className="toast-icon" aria-hidden="true">{icon}</span>
      <span className="toast-text">{toast.text}</span>
      {toast.action && (
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => {
            toast.action?.();
            onDone();
          }}
        >
          {toast.actionLabel ?? t('common.retry')}
        </button>
      )}
      <button type="button" className="icon-btn" onClick={onDone} aria-label={t('common.close')}>
        ×
      </button>
    </div>
  );
}

export function Toasts() {
  const { toasts, dismissToast } = useStore();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDone={() => dismissToast(t.id)} />
      ))}
    </div>
  );
}
