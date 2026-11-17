// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect } from 'react';
import { ENGINE_URL } from './api';
import { APP_NAME } from './brand';
import { ExportDialog } from './components/ExportDialog';
import { ShortcutOverlay } from './components/ShortcutOverlay';
import { Toasts } from './components/Toasts';
import { Button, InlineAlert, isTyping } from './components/ui';
import { useI18n } from './i18n';
import { navigate, useRoute } from './router';
import { Cull } from './screens/Cull';
import { Develop } from './screens/Develop';
import { Films } from './screens/Films';
import { Guide } from './screens/Guide';
import { Library } from './screens/Library';
import { Onboarding } from './screens/Onboarding';
import { Oven } from './screens/Oven';
import { Settings } from './screens/Settings';
import { Viewer } from './screens/Viewer';
import { useStore } from './store';

const MODULES = [
  { route: 'library', key: 'nav.library' },
  { route: 'cull', key: 'nav.cull' },
  { route: 'develop', key: 'nav.develop' },
  { route: 'oven', key: 'nav.oven' },
] as const;

export function App() {
  const route = useRoute();
  const s = useStore();
  const { t, tt } = useI18n();

  // first run → onboarding
  useEffect(() => {
    if (!s.prefs.onboarded && route.name !== 'onboarding' && route.name !== 'guide') navigate('onboarding');
  }, [s.prefs.onboarded, route.name]);

  // global shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && !e.shiftKey && /^[1-6]$/.test(e.key)) {
        e.preventDefault();
        const map = ['library', 'cull', 'develop', 'oven', 'oven', 'films'];
        navigate(map[Number(e.key) - 1]);
        return;
      }
      if (mod && e.key === ',') {
        e.preventDefault();
        navigate('settings');
        return;
      }
      if (mod && e.shiftKey && (e.key === 'E' || e.key === 'e')) {
        e.preventDefault();
        const picks = s.photos.filter((p) => p.flag === 'pick').map((p) => p.id);
        s.setExportOpen(picks);
        return;
      }
      if (isTyping(e) || mod || e.altKey) return;
      if (e.key === '?') {
        e.preventDefault();
        s.setShortcutsOpen(!s.shortcutsOpen);
      } else if ((e.key === 'g' || e.key === 'G') && route.name === 'viewer') {
        navigate('library');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [s, route.name]);

  if (route.name === 'onboarding') {
    return (
      <>
        <Onboarding />
        <Toasts />
      </>
    );
  }

  const ovenJob = s.activeJobs.find((j) => j.kind === 'batch' || j.kind === 'export') ?? s.activeJobs[0];
  const ovenStatus = ovenJob ? s.jobStatus[ovenJob.id] : null;

  let screen;
  switch (route.name) {
    case 'viewer':
      screen = <Viewer id={route.param} />;
      break;
    case 'cull':
      screen = <Cull />;
      break;
    case 'develop':
      screen = <Develop />;
      break;
    case 'oven':
      screen = <Oven />;
      break;
    case 'films':
      screen = <Films />;
      break;
    case 'settings':
      screen = <Settings />;
      break;
    case 'guide':
      screen = <Guide />;
      break;
    default:
      screen = <Library />;
  }

  const activeModule = route.name === 'viewer' ? 'library' : route.name;

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        {t('app.skip')}
      </a>
      <header className="topbar" role="banner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">{APP_NAME}</span>
          {s.session && (
            <span className="session-name" title={s.prefs.lastPath ?? ''}>
              · {s.session.name || s.prefs.lastPath?.split('/').filter(Boolean).pop()}
            </span>
          )}
        </div>
        <nav aria-label={t('nav.modules')}>
          <div role="tablist" className="module-tabs">
            {MODULES.map((m, i) => (
              <a
                key={m.route}
                role="tab"
                aria-selected={activeModule === m.route}
                className="module-tab"
                href={`#/${m.route}`}
                title={`${t(m.key)} (⌘${i + 1})`}
              >
                {t(m.key)}
                {m.route === 'oven' && ovenStatus && <span className="tab-pill">{Math.round((ovenStatus.progress ?? 0) * 100)} %</span>}
              </a>
            ))}
          </div>
        </nav>
        <div className="topbar-right">
          <a className={`icon-link${route.name === 'films' ? ' is-active' : ''}`} href="#/films" title={`${t('nav.films')} (⌘6)`}>
            <span aria-hidden="true">🎞</span>
            <span className="icon-link-label">{t('nav.films')}</span>
          </a>
          <a className={`icon-link${route.name === 'settings' ? ' is-active' : ''}`} href="#/settings" title={`${t('nav.settings')} (⌘,)`}>
            <span aria-hidden="true">⚙</span>
            <span className="icon-link-label">{t('nav.settings')}</span>
          </a>
          <button type="button" className="icon-link" onClick={() => s.setShortcutsOpen(true)} title={t('nav.help')}>
            <span aria-hidden="true">?</span>
            <span className="sr-only">{t('nav.help')}</span>
          </button>
        </div>
      </header>

      {s.healthState === 'offline' && (
        <div className="offline-banner">
          <InlineAlert tone="error" action={<Button size="sm" onClick={() => s.refreshHealth()}>{t('common.retry')}</Button>}>
            <strong>{t('engine.offline.title')}</strong> {t('engine.offline.body', { url: ENGINE_URL })}
          </InlineAlert>
        </div>
      )}

      <main id="main" className="main" tabIndex={-1}>
        {screen}
      </main>

      <footer className="statusbar" role="contentinfo">
        <span className={`status-dot ${s.healthState}`} aria-hidden="true" />
        <span>
          {s.healthState === 'ok'
            ? `${t('status.engineOk')} · v${s.health?.version ?? '?'}`
            : s.healthState === 'checking'
              ? t('status.engineChecking')
              : t('status.engineOff')}
        </span>
        {s.health && (
          <>
            <span className="sep">·</span>
            <span>RawTherapee {s.health.rawtherapee ? '✓' : '✗'}</span>
            <span className="sep">·</span>
            <span>ExifTool {s.health.exiftool ? '✓' : '✗'}</span>
            <span className="sep">·</span>
            <span>{s.aiProvider ? t('status.ai', { p: s.aiProvider }) : t('status.noAi')}</span>
          </>
        )}
        <span className="sep">·</span>
        <span>{s.session ? `${t('status.session')}: ${s.session.count} ${tt('fotos', 'photos')}` : t('status.noSession')}</span>
        <span className="grow" />
        {ovenStatus ? (
          <a href="#/oven" className="status-oven">
            {t('status.ovenRunning', { p: Math.round((ovenStatus.progress ?? 0) * 100) })}
            {ovenStatus.stage ? ` · ${ovenStatus.stage}` : ''}
          </a>
        ) : (
          <span>{t('status.ovenIdle')}</span>
        )}
        <span className="sep">·</span>
        <span title={s.wsConnected ? '' : tt('Sin WebSocket: se consulta el estado cada 1,5 s.', 'No WebSocket: status is polled every 1.5 s.')}>
          {s.wsConnected ? `● ${t('status.live')}` : `○ ${t('status.poll')}`}
        </span>
      </footer>

      <Toasts />
      {s.shortcutsOpen && <ShortcutOverlay onClose={() => s.setShortcutsOpen(false)} />}
      {s.exportOpen && <ExportDialog ids={s.exportOpen} onClose={() => s.setExportOpen(null)} />}
    </div>
  );
}
