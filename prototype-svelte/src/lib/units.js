/**
 * Conversión de unidades para la presentación de los mapas.
 *
 * Solo afecta a lo que se escribe en pantalla: el color se escala con el
 * `vmin`/`vmax` que manda el backend y no se toca. Cada magnitud se identifica
 * por la unidad nativa del producto —la misma cadena que viaja en la cabecera
 * del frame—, así que la tabla sirve igual para la leyenda y para el globo del
 * cursor sin tener que casar ids de producto.
 */

/**
 * Familias convertibles. `native` es la unidad en la que llegan los datos y su
 * etiqueta la pone el propio producto: agua precipitable y nieve vienen en
 * kg/m², que es milímetros de agua con otro nombre.
 */
export const unitFamilies = {
  temperature: {
    label: 'Temperatura',
    bases: ['°C'],
    native: '°C',
    order: ['°C', '°F', 'K'],
    units: {
      '°C': { label: '°C', convert: (value) => value, digits: 1 },
      '°F': { label: '°F', convert: (value) => value * 9 / 5 + 32, digits: 1 },
      K: { label: 'K', convert: (value) => value + 273.15, digits: 1 }
    }
  },
  speed: {
    label: 'Velocidad',
    bases: ['m/s'],
    native: 'm/s',
    order: ['m/s', 'km/h', 'kt', 'mph'],
    units: {
      'm/s': { label: 'm/s', convert: (value) => value, digits: 1 },
      'km/h': { label: 'km/h', convert: (value) => value * 3.6, digits: 0 },
      kt: { label: 'kt', convert: (value) => value * 3.6 / 1.852, digits: 0 },
      mph: { label: 'mph', convert: (value) => value * 3.6 / 1.609344, digits: 0 }
    }
  },
  precipitation: {
    label: 'Precipitación',
    bases: ['mm', 'kg/m²'],
    native: 'mm',
    order: ['mm', 'in'],
    units: {
      mm: { label: 'mm', convert: (value) => value, digits: 1 },
      in: { label: 'in', convert: (value) => value / 25.4, digits: 2 }
    }
  },
  height: {
    label: 'Altura',
    bases: ['m'],
    native: 'm',
    order: ['m', 'ft'],
    units: {
      m: { label: 'm', convert: (value) => value, digits: 0 },
      ft: { label: 'ft', convert: (value) => value / 0.3048, digits: 0 }
    }
  }
};

const familyByBase = new Map();
for (const [id, family] of Object.entries(unitFamilies)) {
  for (const base of family.bases) familyByBase.set(base, { id, ...family });
}

export const defaultUnitPreferences = Object.fromEntries(
  Object.entries(unitFamilies).map(([id, family]) => [id, family.native])
);

/**
 * Familia de un producto, o null si su unidad no admite alternativa.
 *
 * `unitFixed` deja fuera los índices que se expresan en grados sin ser una
 * temperatura: Vertical Totals es una diferencia entre dos niveles, así que
 * pasarla a °F con el desplazamiento de 32 daría un número que no significa
 * nada. Lo mismo vale para el Lifted Index del contorno.
 */
export function unitFamilyOf(product) {
  if (!product || product.unitFixed) return null;
  return familyByBase.get(product.unit) || null;
}

/** Unidad elegida para ese producto, con la nativa como respaldo. */
export function activeUnit(product, preferences) {
  const family = unitFamilyOf(product);
  if (!family) return null;
  const chosen = preferences?.[family.id];
  return family.units[chosen] ? chosen : family.native;
}

/** Etiqueta de una unidad; la nativa conserva el nombre que usa el producto. */
export function unitLabel(product, unit) {
  const family = unitFamilyOf(product);
  if (!family || !unit) return product?.unit || '';
  if (unit === family.native) return product.unit;
  return family.units[unit]?.label || unit;
}

/** Todas las unidades ofrecidas para un producto, ya etiquetadas. */
export function unitOptions(product) {
  const family = unitFamilyOf(product);
  if (!family) return [];
  return family.order.map((unit) => ({ unit, label: unitLabel(product, unit) }));
}

/** Valor convertido a la unidad pedida. Sin familia se devuelve tal cual. */
export function convertValue(value, product, unit) {
  const family = unitFamilyOf(product);
  if (!family || !Number.isFinite(value)) return value;
  return (family.units[unit] || family.units[family.native]).convert(value);
}

function trimZeros(text) {
  return text.includes('.') ? text.replace(/\.?0+$/, '') : text;
}

/**
 * Número ya convertido y redondeado según la unidad.
 *
 * `digits` fuerza los decimales cuando el producto los pide —SHIP se lee con
 * dos— y en su ausencia manda la unidad: los nudos y los km/h no ganan nada
 * con decimales y las pulgadas no se entienden sin ellos.
 */
export function formatValue(value, product, unit, digits) {
  if (!Number.isFinite(value)) return '—';
  const family = unitFamilyOf(product);
  const scale = family?.units[unit] || family?.units[family.native];
  const converted = scale ? scale.convert(value) : value;
  const decimals = digits ?? scale?.digits ?? 1;
  return trimZeros(converted.toFixed(decimals));
}

/** Extremo de la leyenda: los enteros del producto no se llenan de decimales. */
export function formatBound(value, product, unit) {
  if (!Number.isFinite(value)) return '—';
  const converted = convertValue(value, product, unit);
  return trimZeros(converted.toFixed(Math.abs(converted) >= 100 ? 0 : 1));
}
