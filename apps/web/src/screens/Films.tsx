// SPDX-License-Identifier: GPL-3.0-or-later
// Film library.
import { useCallback, useEffect, useState } from 'react';
import { api, type Film } from '../api';
import { Badge, Button, EmptyState, InlineAlert, Segmented, Skeleton, SoonButton } from '../components/ui';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore } from '../store';
import { filmUsable, tierBadge } from './Develop';

export function Films() {
  const s = useStore();
  const { t, tt } = useI18n();
  const [films, setFilms] = useState<Film[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'color' | 'bw'>('all');
  const [sel, setSel] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    setFilms(null);
    api
      .films()
      .then((all) => {
        // "none" (neutral, no film) is a Develop choice, not a stock in the scan library
        const f = all.filter((x) => x.id !== 'none');
        setFilms(f);
        setSel((cur) => cur ?? f[0]?.id ?? null);
      })
      .catch((e) => setErr(s.errText(e)));
  }, [s]);
  useEffect(load, [load]);

  const list = (films ?? []).filter((f) => filter === 'all' || f.type === filter);
  const film = (films ?? []).find((f) => f.id === sel);

  return (
    <div className="workspace">
      <aside className="panel panel-left wide" aria-label={t('films.title')}>
        <div className="panel-heading">
          <h3>{t('films.title')}</h3>
        </div>
        <div className="pad">
          <Segmented
            size="sm"
            label={t('films.title')}
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: t('films.filter.all') },
              { value: 'color', label: t('films.color') },
              { value: 'bw', label: t('films.bw') },
            ]}
          />
        </div>
        {err && (
          <InlineAlert tone="error" action={<Button size="sm" onClick={load}>{t('common.retry')}</Button>}>
            {t('err.films')} {err}
          </InlineAlert>
        )}
        {!films && !err && (
          <div className="pad">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} w="100%" h={40} />
            ))}
          </div>
        )}
        <ul className="film-list" role="listbox" aria-label={t('films.title')}>
          {list.map((f) => (
            <li key={f.id}>
              <button type="button" role="option" aria-selected={f.id === sel} className={`film-row${f.id === sel ? ' is-selected' : ''}`} onClick={() => setSel(f.id)}>
                <span className="film-row-name">{f.name}</span>
                <span className="muted small">{t('films.scans', { n: f.scans })}</span>
                {tierBadge(f, tt)}
              </button>
            </li>
          ))}
        </ul>
        <div className="pad">
          <SoonButton size="sm" label={t('films.add')} why={t('films.add.soon')} />
        </div>
      </aside>
      <section className="stage" aria-label={film?.name ?? t('films.title')}>
        {films && !films.length ? (
          <EmptyState icon="🎞" title={t('films.empty')} />
        ) : !film ? (
          <div className="pad">
            <Skeleton w="50%" h={24} />
          </div>
        ) : (
          <div className="page">
            <div className="row">
              <h1 className="title">{film.name}</h1>
              {tierBadge(film, tt)}
              <Badge>{film.type === 'bw' ? t('films.bw') : t('films.color')}</Badge>
              {filmUsable(film) ? (
                <Badge tone="ok" icon="✓">
                  {tt('Revelado disponible', 'Development available')}
                </Badge>
              ) : (
                <Badge tone="warn" icon="▲">
                  {tt('Perfil aún no entrenado', 'Profile not trained yet')}
                </Badge>
              )}
            </div>
            <div className="film-hero">
              {film.swatch_url ? (
                <img src={api.assetUrl(film.swatch_url) ?? ''} alt={tt('Muestra de {f}', 'Swatch of {f}', { f: film.name })} />
              ) : (
                <div className="ba-placeholder">{tt('Sin escaneos en la biblioteca de películas.', 'No scans in the film library.')}</div>
              )}
            </div>
            <p>
              <strong>{t('films.scans', { n: film.scans })}</strong> · {t('films.private')}
            </p>
            <h2 className="heading">{t('films.quality')}</h2>
            <p className="muted">
              {film.tier === 'spec'
                ? tt('Calibrado y validado en un trabajo real de referencia.', 'Calibrated and validated on a real reference job.')
                : film.scans < 3
                  ? t('films.quality.est')
                  : tt('Estimado a partir de tus escaneos. La puntuación de fidelidad llegará con el entrenamiento.', 'Estimated from your scans. The fidelity score comes with training.')}
            </p>
            <div className="row">
              <Button
                variant="primary"
                disabled={!filmUsable(film) || !s.session}
                why={!filmUsable(film) ? tt('Perfil aún no entrenado.', 'Profile not trained yet.') : tt('Abre una carpeta primero.', 'Open a folder first.')}
                onClick={() => {
                  s.setUi((u) => ({ recipe: { ...(u.recipe ?? {}), film: film.id } }));
                  navigate('develop');
                }}
              >
                {t('films.use')}
              </Button>
              <SoonButton label={t('films.train')} why={t('films.train.soon')} />
            </div>
            <p className="field-help">{t('films.train.soon')}</p>
          </div>
        )}
      </section>
    </div>
  );
}
