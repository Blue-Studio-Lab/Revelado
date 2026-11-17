// SPDX-License-Identifier: GPL-3.0-or-later
// Bilingual UI (ES default, EN). Keyed strings live in strings.ts;
// `tt(es, en)` is for one-off phrases next to the
// component that uses them. Placeholders: {n}, {t}, {file}…
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import type { Lang } from './prefs';
import { fmtDuration, translate, type StringKey } from './strings';

type Vars = Record<string, string | number>;

export interface I18n {
  lang: Lang;
  t: (key: StringKey, vars?: Vars) => string;
  tt: (es: string, en: string, vars?: Vars) => string;
  num: (n: number, digits?: number) => string;
  dur: (seconds: number | null | undefined) => string;
}

function fill(s: string, vars?: Vars) {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

export function makeI18n(lang: Lang): I18n {
  const locale = lang === 'es' ? 'es-ES' : 'en-GB';
  const cache = new Map<number, Intl.NumberFormat>();
  const num = (n: number, digits = 0) => {
    let f = cache.get(digits);
    if (!f) {
      f = new Intl.NumberFormat(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
      cache.set(digits, f);
    }
    return f.format(n);
  };
  return {
    lang,
    t: (key, vars) => translate(lang, key, vars),
    tt: (es, en, vars) => fill(lang === 'es' ? es : en, vars),
    num,
    dur: (s) => fmtDuration(lang, s),
  };
}

const Ctx = createContext<I18n>(makeI18n('es'));

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo(() => makeI18n(lang), [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
