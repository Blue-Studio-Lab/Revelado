// SPDX-License-Identifier: GPL-3.0-or-later
// "?" overlay — searchable list of keyboard shortcuts.
import { useMemo, useState } from 'react';
import { useI18n } from '../i18n';
import { Dialog } from './ui';

type Row = [keys: string, es: string, en: string];

export const SHORTCUTS: { group: 'keys.global' | 'keys.grid' | 'keys.viewer' | 'keys.develop'; rows: Row[] }[] = [
  {
    group: 'keys.global',
    rows: [
      ['⌘/Ctrl + 1…4', 'Ir a Biblioteca / Selección / Cuarto oscuro / Horno', 'Go to Library / Cull / Develop / Batch'],
      ['⌘/Ctrl + 6', 'Películas', 'Film stocks'],
      ['⌘/Ctrl + ,', 'Ajustes', 'Settings'],
      ['⌘/Ctrl + Shift + E', 'Exportar elegidas', 'Export picks'],
      ['?', 'Esta ayuda', 'This help'],
      ['Esc', 'Cerrar diálogo · volver a la cuadrícula', 'Close dialog · back to grid'],
    ],
  },
  {
    group: 'keys.grid',
    rows: [
      ['← → ↑ ↓', 'Mover foco', 'Move focus'],
      ['Inicio / Fin', 'Primera / última', 'First / last'],
      ['Enter · E', 'Abrir en el visor', 'Open in viewer'],
      ['0–5', 'Estrellas', 'Stars'],
      ['P / X / U', 'Elegir / Descartar / Quitar marca', 'Pick / Reject / Unflag'],
    ],
  },
  {
    group: 'keys.viewer',
    rows: [
      ['← →', 'Anterior / siguiente', 'Previous / next'],
      ['Z', 'Encajar ↔ 100 %', 'Fit ↔ 100 %'],
      ['M', 'Siguiente máscara', 'Next mask'],
      ['Alt + 1…3', 'Ruido / Aberración / Sombreado', 'Noise / CA / Shading'],
      ['[ / ]', 'Opacidad de la máscara', 'Mask opacity'],
      ['I', 'Panel de información', 'Info panel'],
      ['0–5 · P / X / U', 'Valorar y marcar', 'Rate and flag'],
      ['G · Esc', 'Volver a la cuadrícula', 'Back to grid'],
    ],
  },
  {
    group: 'keys.develop',
    rows: [
      ['\\', 'Antes / después (alternar)', 'Before / after toggle'],
      ['Y', 'Vista dividida', 'Split view'],
      ['Shift + Y', 'Lado a lado', 'Side by side'],
      ['← → (en el divisor)', 'Mover divisor 5 % (Shift 20 %)', 'Move divider 5 % (Shift 20 %)'],
      ['⌘/Ctrl + Shift + R', 'Revelar muestra', 'Develop sample'],
      ['⌘/Ctrl + Enter', 'Revelar lote → Horno', 'Develop batch → Batch'],
      ['Doble clic en etiqueta', 'Restablecer control', 'Reset control'],
    ],
  },
];

export function ShortcutOverlay({ onClose }: { onClose: () => void }) {
  const { t, lang } = useI18n();
  const [q, setQ] = useState('');
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return SHORTCUTS.map((g) => ({
      ...g,
      rows: g.rows.filter((r) => !needle || `${r[0]} ${r[1]} ${r[2]}`.toLowerCase().includes(needle)),
    })).filter((g) => g.rows.length);
  }, [q]);
  return (
    <Dialog title={t('keys.title')} onClose={onClose} wide>
      <input
        className="input"
        type="search"
        placeholder={t('keys.search')}
        aria-label={t('keys.search')}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="shortcut-groups">
        {groups.map((g) => (
          <section key={g.group}>
            <h3 className="heading">{t(g.group)}</h3>
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
      </div>
    </Dialog>
  );
}
