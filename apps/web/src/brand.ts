// SPDX-License-Identifier: GPL-3.0-or-later
// Single source of truth for the product identity in the UI.
// Renaming the app = editing this file (plus index.html <title>).
export const APP_NAME = 'Revelado';
export const APP_VERSION = '0.1.0';
export const REPO_URL = 'https://github.com/Blue-Studio-Lab/Revelado';
export const LICENSE_ID = 'GPL-3.0-or-later';
export const AUTHOR = 'Jorge Luis Peña Sanchez';
export const AUTHOR_GITHUB = 'https://github.com/bluestudiobrooks-gif';
export const ORG_URL = 'https://github.com/Blue-Studio-Lab';

/** localStorage prefix for this app, and earlier prefixes migrated on start. */
export const STORAGE_PREFIX = 'revelado.';
export const LEGACY_STORAGE_PREFIXES = ['darkroom.', 'cameraman.'];
