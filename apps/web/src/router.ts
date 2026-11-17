// SPDX-License-Identifier: GPL-3.0-or-later
// Minimal hash router (no dependency). Routes:
//   #/onboarding  #/library  #/viewer/:id  #/cull  #/develop  #/oven
//   #/films  #/settings  #/guide/claude-code
import { useEffect, useState } from 'react';

export interface Route {
  name: string;
  param: string | null;
}

export function parseHash(hash: string): Route {
  const clean = hash.replace(/^#\/?/, '');
  const [name = '', ...rest] = clean.split('/');
  return { name: name || 'library', param: rest.length ? decodeURIComponent(rest.join('/')) : null };
}

export function navigate(path: string): void {
  const target = path.startsWith('#') ? path : `#/${path.replace(/^\//, '')}`;
  if (window.location.hash !== target) window.location.hash = target;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
