// SPDX-License-Identifier: GPL-3.0-or-later
// Export dialog wired to POST /api/export (contract addendum).
import { useMemo, useState } from 'react';
import { api, ApiError, type ExportOptions } from '../api';
import { canDevelop } from '../capabilities';
import { absolutePath } from '../paths';
import { useI18n } from '../i18n';
import { navigate } from '../router';
import { useStore } from '../store';
import { Button, Dialog, InlineAlert, Segmented } from './ui';

import { EXPORT_DEST_KEY, EXPORT_OPTS_KEY } from '../storage';
const DEST_KEY = EXPORT_DEST_KEY;
const OPTS_KEY = EXPORT_OPTS_KEY;

function readLocal<T>(k: string, fallback: T): T {
  try {
    const v = localStorage.getItem(k);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function writeLocal(k: string, v: unknown) {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* ignore */
  }
}

export function ExportDialog({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const s = useStore();
  const { t, tt } = useI18n();
  const saved = readLocal<Partial<ExportOptions> & { sizeMode?: 'full' | 'long' }>(OPTS_KEY, {});
  // the engine's resolved folder (absolute) first; the typed path ("~/fotos") only expanded with the engine's home
  const home = typeof s.health?.home === 'string' ? s.health.home : null;
  const sourceDir = (typeof s.session?.path === 'string' && s.session.path) || absolutePath(s.prefs.lastPath ?? '', home) || '';
  const [dest, setDest] = useState<string>(() => {
    const def = sourceDir ? sourceDir.replace(/\/+$/, '') + '_Entrega' : '';
    const saved = readLocal<string>(DEST_KEY, def);
    return absolutePath(saved, home) ?? saved;
  });
  const [format, setFormat] = useState<'jpg' | 'tiff16'>(saved.format ?? 'jpg');
  const [quality, setQuality] = useState<number>(saved.quality ?? 97);
  const [sizeMode, setSizeMode] = useState<'full' | 'long'>(saved.sizeMode ?? 'full');
  const [longEdge, setLongEdge] = useState<number>(saved.long_edge ?? 2560);
  const [naming, setNaming] = useState<ExportOptions['naming']>(saved.naming ?? 'original');
  const [developMissing, setDevelopMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const photos = useMemo(() => ids.map((id) => s.photoById(id)).filter((p): p is NonNullable<typeof p> => !!p), [ids, s]);
  const developed = photos.filter((p) => (p.outputs ?? 0) > 0);
  const missing = photos.filter((p) => !(p.outputs ?? 0) && canDevelop(p, s.caps));
  const noRaw = photos.filter((p) => !(p.outputs ?? 0) && !canDevelop(p, s.caps));
  const toExport = developMissing ? [...developed, ...missing] : developed;

  const filmName = String(s.ui.recipe?.film ?? 'film').replace(/^kodak_|^ilford_/, '');
  const first = toExport[0]?.name?.replace(/\.[^.]+$/, '') ?? 'DSC00001';
  const ext = format === 'jpg' ? 'jpg' : 'tif';
  const example = naming === 'sequence' ? `001_${first}.${ext}` : naming === 'original_film' ? `${first}_${filmName}.${ext}` : `${first}.${ext}`;

  const destTrim = dest.trim();
  // what is sent: always absolute ("~/x" expanded); null while it cannot be made absolute
  const destAbs = absolutePath(destTrim, home);
  const destInvalid = !destAbs;
  const destShown = destAbs ?? destTrim;
  const srcRoot = sourceDir.replace(/\/+$/, '');
  const insideOriginals = !!srcRoot && (destShown.replace(/\/+$/, '') === srcRoot || destShown.startsWith(srcRoot + '/'));
  const absHelp = tt('Usa una ruta absoluta (p. ej. /Users/…/Entrega).', 'Use an absolute path (e.g. /Users/…/Delivery).');
  // a disabled Export button always says why
  const why =
    toExport.length === 0
      ? t('exp.nothing')
      : !destTrim
        ? tt('Escribe la carpeta de destino.', 'Enter the destination folder.')
        : destInvalid
          ? absHelp
          : insideOriginals
            ? t('exp.destSame')
            : s.healthState !== 'ok'
              ? tt('El motor no está conectado.', 'The engine is not connected.')
              : undefined;
  const canGo = !why && !busy && !!destAbs;

  const go = async () => {
    setBusy(true);
    setError(null);
    const opts: ExportOptions = {
      photo_ids: toExport.map((p) => p.id),
      dest_dir: destAbs ?? destTrim,
      format,
      quality,
      long_edge: sizeMode === 'long' ? longEdge : null,
      naming,
      develop_missing: developMissing,
      recipe: developMissing ? s.ui.recipe : null,
    };
    writeLocal(DEST_KEY, opts.dest_dir);
    writeLocal(OPTS_KEY, { format, quality, sizeMode, long_edge: longEdge, naming });
    try {
      const { job_id } = await api.exportPhotos(opts);
      s.trackJob({
        id: job_id,
        kind: 'export',
        label: tt('Exportación de {n} → {d}', 'Export of {n} → {d}', { n: opts.photo_ids.length, d: opts.dest_dir }),
        createdAt: Date.now(),
        photoIds: opts.photo_ids,
      });
      s.toast({ kind: 'info', text: t('exp.started') });
      onClose();
      navigate('oven');
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405 || e.status === 501)) setError(t('exp.unavailable'));
      else setError(s.errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={toExport.length === 1 ? t('exp.title1') : t('exp.title', { n: toExport.length })}
      onClose={onClose}
      footer={
        <>
          {why && !busy && (
            <span className="field-help export-why" role="status" data-testid="export-why">
              {why}
            </span>
          )}
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" onClick={go} disabled={!canGo} loading={busy} why={why} data-testid="export-go">
            {t('exp.go')}
          </Button>
        </>
      }
    >
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          if (canGo) go();
        }}
      >
        <fieldset className="field">
          <legend className="heading">{t('exp.source')}</legend>
          <p className="muted">
            {t('exp.source.developed', { n: developed.length })}
            {missing.length > 0 && ` · ${tt('{n} elegidas sin revelar', '{n} picks not developed yet', { n: missing.length })}`}
            {noRaw.length > 0 && ` · ${t('cull.sendNoRaw', { n: noRaw.length })}`}
          </p>
          {missing.length > 0 && (
            <label className="check">
              <input type="checkbox" checked={developMissing} onChange={(e) => setDevelopMissing(e.target.checked)} />
              {tt(
                'Revelar antes las {n} que faltan con la receta actual (≈ {t}, una cada vez)',
                'Develop the {n} missing ones first with the current recipe (≈ {t}, one at a time)',
                { n: missing.length, t: s.ui.recipe ? `${Math.round((missing.length * 47) / 60)} min` : '—' },
              )}
            </label>
          )}
        </fieldset>

        <label className="field">
          <span className="heading">{t('exp.dest')}</span>
          <input
            className={`input mono${insideOriginals || (destTrim && destInvalid) ? ' is-invalid' : ''}`}
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            placeholder="/Users/…/Entrega"
            spellCheck={false}
            aria-describedby="exp-dest-help"
          />
          <span id="exp-dest-help" className={insideOriginals ? 'field-error' : 'field-help'}>
            {insideOriginals
              ? t('exp.destSame')
              : destTrim && destInvalid
                ? absHelp
                : destAbs && destAbs !== destTrim
                  ? `→ ${destAbs}`
                  : t('exp.destHelp')}
          </span>
        </label>

        <div className="field">
          <span className="heading">{t('exp.format')}</span>
          <Segmented
            label={t('exp.format')}
            value={format}
            onChange={setFormat}
            options={[
              { value: 'jpg', label: t('exp.jpg') },
              { value: 'tiff16', label: t('exp.tiff') },
            ]}
          />
          {format === 'tiff16' && s.caps.tiffRotationNote && (
            <p className="field-help" data-testid="tiff-rotation-note">
              {t('exp.tiffRotation')}
            </p>
          )}
        </div>

        <label className="field">
          <span className="heading">
            {t('exp.quality')} <output className="num">{format === 'jpg' ? quality : '—'}</output>
          </span>
          <input
            type="range"
            min={60}
            max={100}
            step={1}
            value={quality}
            disabled={format !== 'jpg'}
            onChange={(e) => setQuality(Number(e.target.value))}
            aria-valuetext={String(quality)}
          />
          <span className="field-help">{format === 'jpg' ? tt('JPEG 4:4:4: el submuestreo de croma emborrona el grano.', 'JPEG 4:4:4: chroma subsampling smears grain.') : t('exp.tiffQuality')}</span>
        </label>

        <div className="field">
          <span className="heading">{t('exp.size')}</span>
          <div className="row">
            <Segmented
              label={t('exp.size')}
              value={sizeMode}
              onChange={setSizeMode}
              options={[
                { value: 'full', label: t('exp.full') },
                { value: 'long', label: t('exp.long') },
              ]}
            />
            {sizeMode === 'long' && (
              <label className="inline-num">
                <input
                  className="input num"
                  type="number"
                  min={256}
                  max={12000}
                  step={10}
                  value={longEdge}
                  onChange={(e) => setLongEdge(Math.max(256, Number(e.target.value) || 2560))}
                  aria-label={t('exp.long')}
                />
                px
              </label>
            )}
          </div>
        </div>

        <label className="field">
          <span className="heading">{t('exp.naming')}</span>
          <select className="input" value={naming} onChange={(e) => setNaming(e.target.value as ExportOptions['naming'])}>
            <option value="original">{tt('Nombre original', 'Original name')}</option>
            <option value="original_film">{tt('Original + película', 'Original + film stock')}</option>
            <option value="sequence">{tt('Número de orden + original', 'Sequence number + original')}</option>
          </select>
          <span className="field-help">
            {t('exp.naming.example', { ex: example })} · {tt('Nunca se sobrescribe: si existe, se añade _1, _2…', 'Never overwrites: existing names get _1, _2…')}
          </span>
        </label>

        <p className="field-help">
          {tt('Se copia el EXIF del RAW sin GPS. Escritura atómica: un archivo en la carpeta siempre está completo.', 'EXIF is copied from the RAW without GPS. Atomic writes: a file in the folder is always complete.')}
        </p>
        {error && <InlineAlert tone="error">{error}</InlineAlert>}
      </form>
    </Dialog>
  );
}
