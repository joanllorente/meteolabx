import { defaultUnitPreferences, unitFamilies } from './units.js';

const STORAGE_KEY = 'mlx-forecast-units';

function stored() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    // Solo se aceptan familias y unidades que sigan existiendo: una preferencia
    // vieja no puede dejar la leyenda con una unidad que ya no se sabe convertir.
    return Object.fromEntries(
      Object.entries(raw).filter(([family, unit]) => unitFamilies[family]?.units[unit])
    );
  } catch {
    return {};
  }
}

/**
 * Unidad elegida por magnitud, recordada entre visitas.
 *
 * Va por magnitud y no por producto a propósito: quien pone los nudos en el
 * viento a 10 m los quiere también en la racha y en la cizalladura.
 */
export const unitPreferences = $state({ ...defaultUnitPreferences, ...stored() });

export function chooseUnit(family, unit) {
  if (!unitFamilies[family]?.units[unit]) return;
  unitPreferences[family] = unit;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(unitPreferences));
  } catch {
    // Sin almacenamiento —modo privado— la elección vale para esta sesión.
  }
}
