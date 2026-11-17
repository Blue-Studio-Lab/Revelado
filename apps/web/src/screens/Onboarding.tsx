// SPDX-License-Identifier: GPL-3.0-or-later
// Onboarding. No API key ever goes
// through the browser: the key lives in the engine's environment and the
// "Probar / Test" button asks the engine (/api/health) whether it sees one.
import { APP_NAME } from '../brand';
import { useState } from 'react';
import { api, ENGINE_URL } from '../api';
import { Button, InlineAlert, copyText } from '../components/ui';
import { useI18n } from '../i18n';
import type { ProviderChoice } from '../prefs';
import { navigate } from '../router';
import { useStore } from '../store';

const STEPS = 4;

export function AiProviderPicker({ compact }: { compact?: boolean }) {
  const s = useStore();
  const { t, tt } = useI18n();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<'ok' | 'noai' | 'offline' | null>(null);
  const choice = s.prefs.provider;

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const h = await api.health();
      await s.refreshHealth();
      setResult(h?.ai?.provider ? 'ok' : 'noai');
    } catch {
      setResult('offline');
    } finally {
      setTesting(false);
    }
  };

  const cards: { id: ProviderChoice; title: string; desc: string; rec?: boolean }[] = [
    { id: 'claude', title: t('onb.ai.claude'), desc: t('onb.ai.claude.d'), rec: true },
    { id: 'claude_code', title: t('onb.ai.cc'), desc: t('onb.ai.cc.d') },
    { id: 'openai_gemini', title: t('onb.ai.other'), desc: t('onb.ai.other.d') },
    { id: 'ollama', title: t('onb.ai.ollama'), desc: t('onb.ai.ollama.d') },
    { id: 'none', title: t('onb.ai.none'), desc: t('onb.ai.none.d') },
  ];

  return (
    <div className="ai-picker">
      {!compact && <p className="lead">{t('onb.ai.body')}</p>}
      <div className="provider-cards" role="radiogroup" aria-label={t('onb.ai.q')}>
        {cards.map((c) => (
          <button
            type="button"
            key={c.id}
            role="radio"
            aria-checked={choice === c.id}
            className={`provider-card${choice === c.id ? ' is-selected' : ''}`}
            onClick={() => s.setPrefs({ provider: c.id })}
          >
            {c.rec && <span className="provider-rec">★ {t('onb.ai.recommended')}</span>}
            <span className="provider-title">{c.title}</span>
            <span className="provider-desc">{c.desc}</span>
            {(c.id === 'openai_gemini' || c.id === 'ollama') && <span className="soon">{t('common.soon')}</span>}
          </button>
        ))}
      </div>

      <InlineAlert tone="info">
        <strong>{tt('¿Tienes Claude Pro o Max?', 'Have Claude Pro or Max?')}</strong> {t('onb.claude.pro')}{' '}
        <a href="#/guide">{t('onb.cc.open')}</a>
      </InlineAlert>

      {choice === 'claude' && (
        <div className="provider-detail">
          <ol className="steps">
            <li>
              {t('onb.claude.steps1')}{' '}
              <a href="https://platform.claude.com/settings/keys" target="_blank" rel="noreferrer noopener">
                {t('onb.claude.console')}
              </a>
            </li>
            <li>
              {t('onb.claude.steps2')}
              <div className="code-line">
                <code>ANTHROPIC_API_KEY=sk-ant-…</code>
                <Button size="sm" onClick={() => copyText('ANTHROPIC_API_KEY=').then(() => s.toast({ kind: 'success', text: t('common.copied') }))}>
                  {t('common.copy')}
                </Button>
              </div>
            </li>
            <li>{t('onb.claude.steps3')}</li>
          </ol>
          <div className="row">
            <Button onClick={test} loading={testing}>
              {testing ? t('onb.testing') : t('onb.test')}
            </Button>
            <span role="status" className="test-result">
              {result === 'ok' && <span className="ok">✓ {t('onb.test.ok', { p: s.aiProvider ?? 'anthropic' })}</span>}
              {result === 'noai' && <span className="warn">▲ {t('onb.test.noai')}</span>}
              {result === 'offline' && <span className="danger">⊘ {t('onb.test.offline')}</span>}
            </span>
          </div>
          <ul className="checklist">
            <li className={s.healthState === 'ok' ? 'ok' : 'pending'}>
              {s.healthState === 'ok' ? '✓' : '○'} {tt('Motor accesible', 'Engine reachable')} <span className="mono muted">{ENGINE_URL}</span>
            </li>
            <li className={s.aiProvider ? 'ok' : 'pending'}>
              {s.aiProvider ? '✓' : '○'} {tt('Clave vista por el motor', 'Key seen by the engine')}
            </li>
          </ul>
          <p className="field-help">{t('onb.claude.keyNote')}</p>
          <p className="field-help">{t('onb.claude.cost')}</p>
          <p className="field-help">{t('onb.claude.privacy')}</p>
        </div>
      )}
      {choice === 'claude_code' && (
        <div className="provider-detail">
          <p>{t('cc.what')}</p>
          <Button onClick={() => navigate('guide')}>{t('onb.cc.open')} →</Button>
        </div>
      )}
      {choice === 'openai_gemini' && <InlineAlert tone="warn">{t('onb.other.soon')}</InlineAlert>}
      {choice === 'ollama' && <InlineAlert tone="warn">{t('onb.ollama.soon')}</InlineAlert>}
      {choice === 'none' && <InlineAlert tone="success">{t('onb.none.body')}</InlineAlert>}
    </div>
  );
}

export function Onboarding() {
  const s = useStore();
  const { t, tt } = useI18n();
  const [step, setStep] = useState(1);
  const [path, setPath] = useState(s.prefs.lastPath ?? '');
  const h = s.health;

  const finish = async (open: boolean) => {
    s.setPrefs({ onboarded: true });
    if (open && path.trim()) {
      const ok = await s.openFolder(path.trim());
      navigate(ok ? 'library' : 'library');
    } else navigate('library');
  };

  return (
    <div className="onboarding">
      <div className="onb-card">
        <div className="onb-head">
          <span className="brand-mark lg" aria-hidden="true" />
          <div>
            <h1 className="display">{APP_NAME.toUpperCase()}</h1>
            <p className="muted">{t('app.tagline')}</p>
          </div>
          <span className="grow" />
          <span className="muted">{t('onb.step', { i: step, n: STEPS })}</span>
        </div>
        <div className="onb-dots" aria-hidden="true">
          {Array.from({ length: STEPS }).map((_, i) => (
            <span key={i} className={i + 1 <= step ? 'on' : ''} />
          ))}
        </div>

        {step === 1 && (
          <section>
            <h2 className="title">{t('onb.lang.title')}</h2>
            <div className="lang-choice" role="radiogroup" aria-label={t('onb.lang.title')}>
              {(['es', 'en'] as const).map((l) => (
                <label key={l} className={`radio-card${s.prefs.lang === l ? ' is-selected' : ''}`}>
                  <input type="radio" name="lang" checked={s.prefs.lang === l} onChange={() => s.setPrefs({ lang: l })} />
                  {l === 'es' ? 'Español' : 'English'}
                </label>
              ))}
            </div>
          </section>
        )}

        {step === 2 && (
          <section>
            <h2 className="title">{t('onb.check.title')}</h2>
            <ul className="syscheck">
              <li>
                <span className={s.healthState === 'ok' ? 'ok' : s.healthState === 'checking' ? 'muted' : 'danger'}>
                  {s.healthState === 'ok' ? '✓' : s.healthState === 'checking' ? '…' : '⊘'}
                </span>
                <span>{t('onb.check.engine')}</span>
                <span className="muted">
                  {s.healthState === 'ok' ? t('onb.check.ready', { v: h?.version ?? '?' }) : s.healthState === 'checking' ? t('common.loading') : t('status.engineOff')}
                </span>
              </li>
              <li>
                <span className={h?.rawtherapee ? 'ok' : 'warn'}>{h?.rawtherapee ? '✓' : '▲'}</span>
                <span>{t('onb.check.rt')}</span>
                <span className="muted">{h ? (h.rawtherapee ? t('onb.check.found') : t('onb.check.missing')) : '—'}</span>
              </li>
              <li>
                <span className={h?.exiftool ? 'ok' : 'warn'}>{h?.exiftool ? '✓' : '▲'}</span>
                <span>{t('onb.check.exif')}</span>
                <span className="muted">{h ? (h.exiftool ? t('onb.check.found') : t('onb.check.missing')) : '—'}</span>
              </li>
              <li>
                <span className="warn">!</span>
                <span>{tt('Memoria', 'Memory')}</span>
                <span className="muted">{t('onb.check.safe')}</span>
              </li>
            </ul>
            {s.healthState === 'offline' && (
              <InlineAlert tone="error" action={<Button size="sm" onClick={() => s.refreshHealth()}>{t('common.retry')}</Button>}>
                {t('engine.offline.body', { url: ENGINE_URL })}
              </InlineAlert>
            )}
            {h && !h.rawtherapee && <InlineAlert tone="warn">{t('onb.check.rtMissing')}</InlineAlert>}
          </section>
        )}

        {step === 3 && (
          <section>
            <h2 className="title">
              {t('onb.ai.title')} · {t('onb.ai.q')}
            </h2>
            <AiProviderPicker />
          </section>
        )}

        {step === 4 && (
          <section>
            <h2 className="title">{t('onb.first.title')}</h2>
            <p>{t('onb.first.body')}</p>
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                finish(true);
              }}
            >
              <input
                className="input mono grow"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={t('lib.pathPh')}
                aria-label={t('lib.path')}
                spellCheck={false}
              />
              <Button type="submit" variant="primary" disabled={!path.trim() || s.healthState !== 'ok'} loading={s.openState === 'loading'} why={s.healthState !== 'ok' ? t('err.offline') : undefined}>
                {t('lib.open')}
              </Button>
            </form>
            <p className="field-help">{t('lib.pathHelp')}</p>
            {s.prefs.recents.length > 0 && (
              <ul className="recent-list">
                {s.prefs.recents.map((r) => (
                  <li key={r}>
                    <button type="button" className="link-btn mono" onClick={() => setPath(r)}>
                      {r}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        <div className="onb-foot">
          {step > 1 && (
            <Button variant="ghost" onClick={() => setStep(step - 1)}>
              ← {t('common.back')}
            </Button>
          )}
          <span className="grow" />
          {step >= 3 && step < STEPS && (
            <Button variant="ghost" onClick={() => setStep(step + 1)}>
              {t('onb.skip')}
            </Button>
          )}
          {step < STEPS ? (
            <Button variant="primary" onClick={() => setStep(step + 1)}>
              {t('common.continue')} →
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => finish(false)}>
              {t('onb.skip')}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
