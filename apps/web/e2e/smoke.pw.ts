// SPDX-License-Identifier: GPL-3.0-or-later
import { expect, test, type Page } from '@playwright/test';

const base = {
  camera: 'ILCE-7M4', lens: null, iso: 400, shutter: '1/250', aperture: 2.8, focal: 35,
  datetime: '2026-11-15T16:00:00', width: 6000, height: 4000, rating: 0, flag: 'pick',
};
const PHOTOS = [
  { ...base, id: 'both', name: 'DSC0001', raw_path: '/f/DSC0001.ARW', jpg_path: '/f/DSC0001.JPG', has_raw: true, has_jpg: true, can_develop: true, develop_block_reason: null },
  { ...base, id: 'raw', name: 'DSC0002', raw_path: '/f/DSC0002.ARW', jpg_path: null, has_raw: true, has_jpg: false, can_develop: true, develop_block_reason: null },
  { ...base, id: 'jpg', name: 'DSC0003', raw_path: null, jpg_path: '/f/DSC0003.JPG', has_raw: false, has_jpg: true, can_develop: false, develop_block_reason: 'needs_raw' },
];

// What the current engine reports (engine/README.md, contract notes).
const ALL_CAPS = { develop_jpg: true, tiff16_rotation: true, raw_only_ok: true, jpg_only_ok: true };
const NEUTRAL = {
  film: 'none', film_strength: 1, exposure_ev: 0, grain: 0, skin_retouch: 0, blemishes: 0, hair: 'off', accent: 0,
  reframe: 'off', sharpen: 1, nr: 'auto', ca: 'auto', develop_source: 'raw',
};
const EKTAR = { ...NEUTRAL, film: 'kodak_ektar_100', grain: 1, skin_retouch: 60, blemishes: 50, accent: 40, reframe: 'suggest' };
const num = (min: number, max: number, step: number, def: number, label_es: string) => ({ type: 'number', min, max, step, default: def, label_es, supported: true });
const SCHEMA = {
  film: { type: 'string', default: 'none', label_es: 'Película', supported: true },
  film_strength: num(0, 1, 0.05, 1, 'Intensidad'),
  exposure_ev: num(-2, 2, 0.1, 0, 'Exposición'),
  grain: num(0, 1.5, 0.05, 0, 'Grano'),
  skin_retouch: num(0, 100, 1, 0, 'Retoque de piel'),
  blemishes: num(0, 100, 1, 0, 'Granitos'),
  hair: { type: 'enum', options: ['off', 'suave', 'medio', 'fuerte'], default: 'off', label_es: 'Cabello', supported: true },
  accent: num(0, 100, 1, 0, 'Acento sujeto'),
  reframe: { type: 'enum', options: ['off', 'suggest', 'apply'], default: 'off', label_es: 'Encuadre', supported: true },
  sharpen: num(0, 2, 0.05, 1, 'Nitidez'),
  nr: { type: 'enum', options: ['auto', 'soft', 'strong', 'off'], default: 'auto', label_es: 'Ruido', supported: true },
  ca: { type: 'enum', options: ['auto', 'off'], default: 'auto', label_es: 'Aberración', supported: true },
  develop_source: { type: 'enum', options: ['raw', 'jpg'], default: 'raw', label_es: 'Fuente del revelado', supported: true },
};
const FILMS = [
  { id: 'none', name: 'Neutro (sin película)', stock: 'none', type: 'neutral', scans: 0, tier: 'builtin', swatch_url: null, engine_supported: true, status: 'trained' },
  { id: 'kodak_ektar_100', name: 'Kodak Ektar 100', stock: 'kodak_ektar_100', type: 'color', scans: 0, tier: 'spec', swatch_url: null, engine_supported: true, status: 'trained' },
];
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

interface Mock {
  batchBodies: Record<string, unknown>[];
  sampleBodies: Record<string, unknown>[];
  exportBodies: Record<string, unknown>[];
}

async function mockEngine(
  page: Page,
  capabilities: Record<string, boolean>,
  opts: { home?: string; openPath?: string; photos?: unknown[] } = {},
): Promise<Mock> {
  const m: Mock = { batchBodies: [], sampleBodies: [], exportBodies: [] };
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, '');
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/health')
      return json({ ok: true, version: '0.1.0', rawtherapee: true, exiftool: true, ai: null, capabilities, develop_engine: { available: true, engine: 'native', raw: true }, ...(opts.home ? { home: opts.home } : {}) });
    if (path === '/sessions') return json([]);
    if (path === '/library/open') return json({ session_id: 's1', count: 3, pairs: 1, raw_only: 1, jpg_only: 1, path: opts.openPath ?? '/f', name: 'f' });
    if (path === '/photos') return json(opts.photos ?? PHOTOS);
    if (path === '/export') {
      m.exportBodies.push(route.request().postDataJSON());
      return json({ job_id: 'job-export' });
    }
    if (path === '/films') return json(FILMS);
    if (path === '/jobs') return json([]);
    if (path === '/recipe/default') return json(NEUTRAL);
    if (path === '/recipe/schema') return json(SCHEMA);
    if (path === '/recipe/presets')
      return json([
        { id: 'neutral', label_es: 'Neutro', label_en: 'Neutral', recipe: NEUTRAL, default: true },
        { id: 'editorial_ektar', label_es: 'Editorial · Kodak Ektar 100', label_en: 'Editorial · Kodak Ektar 100', recipe: EKTAR, default: false },
      ]);
    const prev = /^\/photos\/([^/]+)\/(preview|thumb)$/.exec(path);
    if (prev) {
      const source = prev[1] === 'raw' ? 'embedded' : 'camera_jpg';
      return route.fulfill({ status: 200, contentType: 'image/png', headers: { 'X-Preview-Source': source }, body: PNG });
    }
    if (path.startsWith('/outputs/')) return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    if (path === '/develop/sample') {
      m.sampleBodies.push(route.request().postDataJSON());
      return json({ job_id: 'job-sample' });
    }
    if (path === '/develop/batch') {
      const body = route.request().postDataJSON();
      m.batchBodies.push(body);
      return json({ job_id: 'job-batch', recipe: body.recipe, recipes: {} });
    }
    if (path === '/jobs/job-sample')
      return json({
        job_id: 'job-sample', kind: 'sample', state: 'done', progress: 1, stage: null, eta_s: null, log: [],
        outputs: [{ photo_id: 'raw', output_id: 'o1', url: '/api/outputs/o1.jpg', web_url: '/api/outputs/o1_web.jpg' }],
        started_at: '2026-11-17T10:00:00Z', finished_at: '2026-11-17T10:00:40Z',
      });
    if (path === '/jobs/job-batch') return json({ job_id: 'job-batch', kind: 'batch', state: 'running', progress: 0.1, stage: 'develop', eta_s: 80, log: [], outputs: [] });
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not mocked"}' });
  });
  return m;
}

async function onboardAndOpen(page: Page, folder = '/f') {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'REVELADO' })).toBeVisible();
  for (let i = 0; i < 3; i++) await page.getByRole('button', { name: /Continuar/ }).click();
  await page.getByLabel('Carpeta').fill(folder);
  await page.getByRole('button', { name: 'Abrir carpeta' }).click();
  await expect(page).toHaveURL(/#\/library/);
}

test('pair badges, JPG-only block reason in Develop, TIFF note hidden when rotation works', async ({ page }) => {
  // Even with develop_jpg/jpg_only_ok true the UI keeps the "needs the RAW" block (develop_source "jpg" is not offered).
  await mockEngine(page, ALL_CAPS);
  await onboardAndOpen(page);

  // Library: one of each pairing, JPG-only marked as not developable.
  await expect(page.locator('.pair-badge[data-pair="both"]')).toHaveCount(1);
  await expect(page.locator('.pair-badge[data-pair="raw"][data-developable="yes"]')).toContainText('Solo RAW');
  await expect(page.locator('.pair-badge[data-pair="jpg"][data-developable="no"]')).toContainText('Solo JPG');

  // Develop: the two RAW photos are in, the JPG-only one is listed with the reason.
  await page.evaluate(() => (location.hash = '#/develop')); // in-app nav, no reload
  const blocked = page.getByTestId('blocked-list');
  await expect(blocked.locator('li')).toHaveCount(1);
  await expect(blocked.locator('li[data-photo="jpg"]')).toContainText('Se necesita el RAW para revelar');

  // Export dialog: no TIFF rotation note when the engine straightens TIFF16.
  await page.evaluate(() => (location.hash = '#/library'));
  await page.getByRole('button', { name: 'Exportar…' }).click();
  await page.getByRole('radio', { name: 'TIFF 16 bits' }).click();
  await expect(page.getByRole('radio', { name: 'TIFF 16 bits' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('tiff-rotation-note')).toHaveCount(0);
});

test('TIFF note shown when the engine reports tiff16_rotation=false', async ({ page }) => {
  await mockEngine(page, { tiff16_rotation: false });
  await onboardAndOpen(page);
  await page.getByRole('button', { name: 'Exportar…' }).click();
  await page.getByRole('radio', { name: 'TIFF 16 bits' }).click();
  await expect(page.getByTestId('tiff-rotation-note')).toBeVisible();
});

test('Develop: neutral default, presets, film picker, RAW preview label, per-photo overrides sent with the batch', async ({ page }) => {
  const m = await mockEngine(page, ALL_CAPS);
  await onboardAndOpen(page);
  await page.evaluate(() => (location.hash = '#/develop'));

  // Neutral is the default preset and "Sin película" is the first, selected film.
  const presets = page.getByRole('radiogroup', { name: 'Preset' });
  await expect(presets.getByRole('radio', { name: 'Neutro' })).toHaveAttribute('aria-checked', 'true');
  await expect(presets.getByRole('radio', { name: 'Editorial Ektar' })).toHaveAttribute('aria-checked', 'false');
  const films = page.locator('.film-picker [role="radio"]');
  await expect(films.first()).toContainText('Sin película');
  await expect(films.first()).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('.soon')).toHaveCount(1); // only "Vista rápida"; NR/CA and neutral are live
  await expect(page.locator('[data-field="nr"] .soon, [data-field="ca"] .soon')).toHaveCount(0);

  // The first sample has a camera JPG: no RAW-preview label.
  await expect(page.getByTestId('before-label')).toHaveText('Antes · JPG de cámara');
  await expect(page.getByTestId('before-label')).toHaveAttribute('data-source', 'camera_jpg');

  // Editorial Ektar fills the whole batch recipe.
  await presets.getByRole('radio', { name: 'Editorial Ektar' }).click();
  await expect(page.locator('[data-film="kodak_ektar_100"]')).toHaveAttribute('aria-checked', 'true');
  await expect(presets.getByRole('radio', { name: 'Editorial Ektar' })).toHaveAttribute('aria-checked', 'true');

  // RAW-only sample: the before image is the RAW preview.
  await page.getByRole('button', { name: 'Cambiar muestra' }).click();
  await page.locator('.sample-strip [data-photo="raw"]').click();
  await expect(page.getByTestId('before-label')).toHaveText('Antes: vista previa del RAW');
  await expect(page.getByTestId('before-label')).toHaveAttribute('data-source', 'embedded');

  // "Solo esta foto": strong NR only for DSC0002.
  await page.getByTestId('scope-this').check();
  await page.getByRole('radiogroup', { name: 'Ruido' }).getByRole('radio', { name: 'Fuerte' }).click();
  await expect(page.getByTestId('sample-override-badge')).toHaveText('Ajustes propios');
  await expect(page.locator('[data-field="nr"].is-overridden')).toHaveCount(1);
  await page.getByRole('button', { name: 'Cambiar muestra' }).click();
  await expect(page.locator('.sample-strip [data-photo="raw"]')).toHaveAttribute('data-override', 'yes');
  await expect(page.locator('.sample-strip [data-photo="both"]')).not.toHaveAttribute('data-override', 'yes');
  await page.getByRole('button', { name: 'Cambiar muestra' }).click();

  // The sample is developed with the photo's own recipe; then the batch carries the override.
  await page.getByRole('button', { name: /Revelar 1 muestra/ }).click();
  await expect.poll(() => m.sampleBodies.length).toBe(1);
  expect(m.sampleBodies[0]).toMatchObject({ photo_id: 'raw', recipe: { film: 'kodak_ektar_100', nr: 'strong' } });
  const batchBtn = page.getByRole('button', { name: /Revelar las 2/ });
  await expect(batchBtn).toBeEnabled();
  await batchBtn.click();
  await expect.poll(() => m.batchBodies.length).toBe(1);
  const body = m.batchBodies[0] as { photo_ids: string[]; recipe: Record<string, unknown>; overrides: Record<string, unknown> };
  expect(body.photo_ids).toEqual(['both', 'raw']);
  expect(body.recipe.film).toBe('kodak_ektar_100');
  expect(body.recipe.nr).toBe('auto');
  expect(body.recipe.develop_source).toBeUndefined();
  expect(body.overrides).toEqual({ raw: { nr: 'strong' } });
  await expect(page).toHaveURL(/#\/oven/);
});

test('Develop: the before label falls back to the photo when the engine sends no X-Preview-Source', async ({ page }) => {
  await mockEngine(page, ALL_CAPS);
  await page.route('**/api/photos/*/preview*', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: PNG }));
  await onboardAndOpen(page);
  await page.evaluate(() => (location.hash = '#/develop'));
  await expect(page.getByTestId('before-label')).toHaveText('Antes · JPG de cámara');
  await page.getByRole('button', { name: 'Cambiar muestra' }).click();
  await page.locator('.sample-strip [data-photo="raw"]').click();
  await expect(page.getByTestId('before-label')).toHaveText('Antes: vista previa del RAW');
});

test('Export: a folder opened as "~/…" gets an absolute default destination, "~/" is expanded before sending, a disabled Export says why', async ({ page }) => {
  const photos = PHOTOS.map((p) => (p.id === 'raw' ? { ...p, outputs: 1 } : p));
  const m = await mockEngine(page, ALL_CAPS, { home: '/srv/ana', openPath: '/srv/ana/revelado-demo/raws', photos });
  await onboardAndOpen(page, '~/revelado-demo/raws');
  await page.getByRole('button', { name: 'Exportar…' }).click();
  const dest = page.locator('input[aria-describedby="exp-dest-help"]');
  await expect(dest).toHaveValue('/srv/ana/revelado-demo/raws_Entrega');
  await expect(page.getByText('Usa una ruta absoluta', { exact: false })).toHaveCount(0);
  await expect(page.getByTestId('export-go')).toBeEnabled();

  // a relative path: Export is disabled and says why, visibly
  await dest.fill('Entrega');
  await expect(page.getByTestId('export-go')).toBeDisabled();
  await expect(page.getByTestId('export-why')).toContainText('ruta absoluta');

  // "~/…" is accepted and sent expanded
  await dest.fill('~/Entrega');
  await expect(page.locator('#exp-dest-help')).toHaveText('→ /srv/ana/Entrega');
  await page.getByTestId('export-go').click();
  await expect.poll(() => m.exportBodies.length).toBe(1);
  expect(m.exportBodies[0].dest_dir).toBe('/srv/ana/Entrega');
});

test('Viewer labels a RAW-only photo as a RAW preview, a paired one as the camera JPG', async ({ page }) => {
  await mockEngine(page, ALL_CAPS);
  await onboardAndOpen(page);
  await page.evaluate(() => (location.hash = '#/viewer/raw'));
  await expect(page.getByTestId('viewer-source')).toHaveText('Vista previa del RAW');
  await page.evaluate(() => (location.hash = '#/viewer/both'));
  await expect(page.getByTestId('viewer-source')).toHaveText('JPG de cámara');
});
