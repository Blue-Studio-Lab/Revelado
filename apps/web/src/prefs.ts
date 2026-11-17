// SPDX-License-Identifier: GPL-3.0-or-later
// Per-viewer UI preferences kept in localStorage (see storage.ts).
// Only conveniences live here (language, recent folders, view options).
// Never API keys or other secrets: those stay in the engine's environment.
import { migrateLegacyKeys, PREFS_KEY, readItem, writeItem } from './storage';

export type Lang = 'es' | 'en';
export type Surround = 'black' | 'dark' | 'mid';
export type ProviderChoice = 'claude' | 'openai_gemini' | 'ollama' | 'claude_code' | 'none';

export interface Prefs {
  lang: Lang;
  onboarded: boolean;
  recents: string[];
  lastPath: string | null;
  thumbSize: number;
  autoAdvance: boolean;
  surround: Surround;
  reduceMotion: boolean;
  uiScale: number; // 1 – 1.5
  provider: ProviderChoice | null;
  aiCull: boolean; // privacy opt-in: AI culling
  aiAdjust: boolean; // privacy opt-in: auto adjust
  aiRescue: boolean; // privacy opt-in: rescue
  useAiThisSession: boolean;
  cullTarget: number;
  apiBase: string; // '' = same-origin /api through the Vite proxy
}

export const defaultPrefs: Prefs = {
  lang: 'es',
  onboarded: false,
  recents: [],
  lastPath: null,
  thumbSize: 180,
  autoAdvance: false,
  surround: 'dark',
  reduceMotion: false,
  uiScale: 1,
  provider: null,
  aiCull: false,
  aiAdjust: false,
  aiRescue: false,
  useAiThisSession: true,
  cullTarget: 150,
  apiBase: '',
};

export function loadPrefs(): Prefs {
  migrateLegacyKeys();
  try {
    const raw = readItem(PREFS_KEY);
    if (!raw) return { ...defaultPrefs };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    return { ...defaultPrefs, ...parsed };
  } catch {
    return { ...defaultPrefs };
  }
}

export function savePrefs(p: Prefs): void {
  writeItem(PREFS_KEY, JSON.stringify(p));
}
