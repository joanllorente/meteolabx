/** Estado global y persistencia de las preferencias de unidades. */
import {
  defaultUnitPreferences,
  normalizeUnitPreferences,
  unitOptions,
  unitPresets
} from './units.js';
import { readSharedLegacyKey } from './legacy-storage.js';

export * from './units.js';

export const UNIT_STORAGE_KEY = 'meteolabx_unit_preferences';

export const unitPreferences = $state({ ...defaultUnitPreferences });

export function loadUnitPreferences() {
  // La clave es la misma que usaba la interfaz anterior, pero aquella la
  // guardaba envuelta en un objeto con la propia clave dentro: hay que pelarla
  // o las unidades elegidas se leen como desconocidas y vuelven a las de
  // fábrica.
  const stored = readSharedLegacyKey(UNIT_STORAGE_KEY) || {};
  Object.assign(unitPreferences, normalizeUnitPreferences(stored));
}

function persistUnits() {
  try {
    localStorage.setItem(UNIT_STORAGE_KEY, JSON.stringify(unitPreferences));
  } catch {
    // En navegación privada se conserva al menos durante esta sesión.
  }
  window.dispatchEvent(new CustomEvent('mlx:units', { detail: { ...unitPreferences } }));
}

export function chooseUnit(family, unit) {
  if (!unitOptions[family]?.[unit]) return;
  unitPreferences[family] = unit;
  persistUnits();
}

/** Aplica un sistema entero de una vez, con un único aviso de cambio. */
export function choosePreset(id) {
  const preset = unitPresets.find((item) => item.id === id);
  if (!preset) return;
  Object.assign(unitPreferences, preset.units);
  persistUnits();
}
