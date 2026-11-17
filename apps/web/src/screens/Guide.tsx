// SPDX-License-Identifier: GPL-3.0-or-later
// Help page: "Usar con Claude Code (Pro/Max)". A guide, not a sign-in.
import { Button, InlineAlert, copyText } from '../components/ui';
import { useI18n } from '../i18n';
import { useStore } from '../store';

export function Guide() {
  const { t, tt } = useI18n();
  const s = useStore();
  const cmd = 'claude mcp add revelado -s user -- uvx revelado-mcp';
  return (
    <div className="page narrow">
      <p>
        <a href={s.prefs.onboarded ? '#/settings' : '#/onboarding'}>← {t('common.back')}</a>
      </p>
      <h1 className="title">{t('cc.title')}</h1>
      <InlineAlert tone="warn">{t('cc.status')}</InlineAlert>
      <p>{t('cc.what')}</p>
      <p className="muted">{t('cc.policy')}</p>
      <ol className="steps">
        <li>
          <strong>{t('cc.step1')}</strong>
          <div className="code-line">
            <code>{cmd}</code>
            <Button size="sm" onClick={() => copyText(cmd).then(() => s.toast({ kind: 'success', text: t('common.copied') }))}>
              {t('common.copy')}
            </Button>
          </div>
          <span className="field-help">{tt('Con uvx funciona cuando revelado-mcp esté en PyPI; antes, usa la instalación desde el código fuente de mcp/README.md.', 'uvx works once revelado-mcp is on PyPI; before that, use the from-source install in mcp/README.md.')}</span>
        </li>
        <li>
          <strong>{t('cc.step2')}</strong>
        </li>
        <li>
          <strong>{t('cc.step3')}</strong>
          <ul>
            <li>“{t('cc.prompt1')}”</li>
            <li>“{t('cc.prompt2')}”</li>
          </ul>
        </li>
      </ol>
      <h2 className="heading">{tt('Qué verás en la app', 'What you will see in the app')}</h2>
      <ul>
        <li>{tt('Un aviso «Controlado desde Claude Code» y cada acción en el registro.', 'A “Driven by Claude Code” banner and every action in the log.')}</li>
        <li>{tt('Los trabajos pesados siguen la misma cola: una foto cada vez.', 'Heavy jobs use the same queue: one photo at a time.')}</li>
        <li>{tt('Puedes desconectarlo cuando quieras.', 'You can disconnect it at any time.')}</li>
      </ul>
    </div>
  );
}
