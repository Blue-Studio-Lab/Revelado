// SPDX-License-Identifier: GPL-3.0-or-later
// Settings.
import { useState } from 'react';
import { ENGINE_URL } from '../api';
import { SHORTCUTS } from '../components/ShortcutOverlay';
import { Button, KV, Segmented } from '../components/ui';
import { useI18n } from '../i18n';
import { APP_NAME, APP_VERSION, AUTHOR, AUTHOR_GITHUB, LICENSE_ID, ORG_URL, REPO_URL } from '../brand';
import { defaultPrefs } from '../prefs';
import { clearAppData } from '../storage';
import { navigate } from '../router';
import { useStore } from '../store';
import { AiProviderPicker } from './Onboarding';

const SECTIONS = ['general', 'ai', 'viewer', 'engine', 'a11y', 'shortcuts', 'about'] as const;
type Sec = (typeof SECTIONS)[number];

export function Settings() {
  const s = useStore();
  const { t, tt, lang } = useI18n();
  const [sec, setSec] = useState<Sec>('general');
  const names: Record<Sec, string> = {
    general: t('set.general'),
    ai: t('set.ai'),
    viewer: t('set.viewer'),
    engine: t('set.engine'),
    a11y: t('set.a11y'),
    shortcuts: t('set.shortcuts'),
    about: t('set.about'),
  };

  return (
    <div className="workspace">
      <nav className="panel panel-left" aria-label={t('set.title')}>
        <div className="panel-heading">
          <h3>{t('set.title')}</h3>
        </div>
        <ul className="settings-nav">
          {SECTIONS.map((x) => (
            <li key={x}>
              <button type="button" className={sec === x ? 'is-selected' : ''} aria-current={sec === x || undefined} onClick={() => setSec(x)}>
                {names[x]}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <section className="stage">
        <div className="page narrow">
          <h1 className="title">{names[sec]}</h1>
          {sec === 'general' && (
            <div className="form-grid">
              <div className="field">
                <span className="heading">{t('set.language')}</span>
                <Segmented
                  label={t('set.language')}
                  value={s.prefs.lang}
                  onChange={(v) => s.setPrefs({ lang: v })}
                  options={[
                    { value: 'es', label: 'Español' },
                    { value: 'en', label: 'English' },
                  ]}
                />
              </div>
              <label className="check">
                <input type="checkbox" checked={s.prefs.autoAdvance} onChange={(e) => s.setPrefs({ autoAdvance: e.target.checked })} />
                {t('set.autoAdvance')}
              </label>
              <label className="field-inline">
                <span>{t('set.target')}</span>
                <input className="input sm num" type="number" min={1} value={s.prefs.cullTarget} onChange={(e) => s.setPrefs({ cullTarget: Math.max(1, Number(e.target.value) || 1) })} style={{ width: 90 }} />
              </label>
              <div className="field">
                <span className="heading">{t('set.data')}</span>
                <p className="field-help">{t('set.data.body')}</p>
                <div className="row">
                  <Button onClick={() => navigate('onboarding')}>{t('set.onboarding')}</Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      clearAppData();
                      s.setPrefs({ ...defaultPrefs, onboarded: true, lang: s.prefs.lang });
                      s.toast({ kind: 'success', text: t('set.data.cleared') });
                    }}
                  >
                    {t('set.data.clear')}
                  </Button>
                </div>
              </div>
            </div>
          )}
          {sec === 'ai' && (
            <>
              <dl className="kv-list">
                <KV k={t('set.ai.provider')} v={s.aiProvider ?? t('set.ai.none')} />
              </dl>
              <p className="field-help">{t('set.ai.how')}</p>
              <AiProviderPicker compact />
            </>
          )}
          {sec === 'viewer' && (
            <div className="field">
              <span className="heading">{t('set.surround')}</span>
              <Segmented
                label={t('set.surround')}
                value={s.prefs.surround}
                onChange={(v) => s.setPrefs({ surround: v })}
                options={[
                  { value: 'black', label: t('set.surround.black') },
                  { value: 'dark', label: t('set.surround.dark') },
                  { value: 'mid', label: t('set.surround.mid') },
                ]}
              />
              <div className="surround-swatch" aria-hidden="true" />
              <p className="field-help">
                {tt(
                  'Los colores de la interfaz son grises neutros (R = G = B). El acento ámbar solo marca controles, nunca la foto.',
                  'UI colours are neutral greys (R = G = B). The amber accent marks controls only, never the photo.',
                )}
              </p>
            </div>
          )}
          {sec === 'engine' && (
            <>
              <dl className="kv-list">
                <KV k={t('set.engine.url')} v={<span className="mono">{ENGINE_URL}</span>} />
                <KV k={t('set.engine.version')} v={s.health?.version ?? '—'} />
                <KV k="RawTherapee CLI" v={s.health ? (s.health.rawtherapee ? '✓' : '✗') : '—'} />
                <KV k="ExifTool" v={s.health ? (s.health.exiftool ? '✓' : '✗') : '—'} />
                <KV k={tt('Eventos en vivo', 'Live events')} v={s.wsConnected ? 'WebSocket ✓' : tt('sondeo cada 1,5 s', 'polling every 1.5 s')} />
              </dl>
              <p className="field-help">{t('set.engine.urlHelp')}</p>
              <p className="field-help">{t('set.perf.safe')}</p>
              <Button onClick={() => s.refreshHealth().then(() => s.toast({ kind: 'info', text: s.healthState === 'ok' ? t('status.engineOk') : t('status.engineOff') }))}>
                {t('set.engine.check')}
              </Button>
            </>
          )}
          {sec === 'a11y' && (
            <div className="form-grid">
              <label className="check">
                <input type="checkbox" checked={s.prefs.reduceMotion} onChange={(e) => s.setPrefs({ reduceMotion: e.target.checked })} />
                {t('set.motion')}
              </label>
              <label className="field">
                <span className="heading">
                  {t('set.textSize')} <output className="num">{Math.round(s.prefs.uiScale * 100)} %</output>
                </span>
                <input
                  type="range"
                  min={100}
                  max={150}
                  step={10}
                  value={Math.round(s.prefs.uiScale * 100)}
                  onChange={(e) => s.setPrefs({ uiScale: Number(e.target.value) / 100 })}
                  aria-valuetext={`${Math.round(s.prefs.uiScale * 100)} %`}
                />
              </label>
            </div>
          )}
          {sec === 'shortcuts' &&
            SHORTCUTS.map((g) => (
              <section key={g.group}>
                <h2 className="heading">{t(g.group)}</h2>
                <table className="shortcut-table">
                  <tbody>
                    {g.rows.map((r) => (
                      <tr key={r[0] + r[1]}>
                        <td>
                          <kbd>{r[0]}</kbd>
                        </td>
                        <td>{lang === 'es' ? r[1] : r[2]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}
          {sec === 'about' && (
            <>
              <h2 className="heading">{APP_NAME}</h2>
              <p className="muted">{t('app.tagline')}</p>
              <p>{t('set.about.body', { name: APP_NAME, v: APP_VERSION, license: LICENSE_ID })}</p>
              <p>
                {t('set.about.credits')}{' '}
                <a href={AUTHOR_GITHUB} target="_blank" rel="noreferrer">
                  {AUTHOR}
                </a>{' '}
                · {t('set.about.cocreated')}
              </p>
              <p>
                <a href={REPO_URL} target="_blank" rel="noreferrer">
                  {REPO_URL.replace('https://', '')}
                </a>{' '}
                ·{' '}
                <a href={ORG_URL} target="_blank" rel="noreferrer">
                  Blue Studio Lab
                </a>
              </p>
              <p className="field-help">{t('lib.originals')}</p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
