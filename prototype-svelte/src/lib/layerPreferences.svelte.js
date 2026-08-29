/**
 * Capas del mapa que el usuario puede apagar, recordadas entre visitas.
 *
 * Van por tipo de capa y no por producto: quien apaga las isotermas para
 * mirar el geopotencial las quiere apagadas en los dos niveles. Todas vienen
 * encendidas; solo se guarda lo que el usuario cambia.
 */

const STORAGE_KEY = 'mlx-forecast-layers';

export const LAYERS = [
  { id: 'isotherms', label: 'Isotermas' },
  { id: 'isohypses', label: 'Isohipsas' },
  { id: 'troughs', label: 'Ejes de vaguada' }
];

const defaults = Object.fromEntries(LAYERS.map((capa) => [capa.id, true]));

function stored() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return Object.fromEntries(
      Object.entries(raw)
        .filter(([id, value]) => id in defaults && typeof value === 'boolean')
    );
  } catch {
    return {};
  }
}

export const layerPreferences = $state({ ...defaults, ...stored() });

export function toggleLayer(id) {
  if (!(id in defaults)) return;
  layerPreferences[id] = !layerPreferences[id];
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layerPreferences));
  } catch {
    // Sin almacenamiento —modo privado— la elección vale para esta sesión.
  }
}
