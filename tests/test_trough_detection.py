"""
Comportamiento de los detectores del visor: vaguadas y centros de presión.

Las otras pruebas del visor comprueban que el código diga lo que dice; estas
lo ejecutan contra campos sintéticos y miran lo que devuelve. El detector vive
en JavaScript, así que se corre con node, que ya hace falta para compilar el
visor. Sin node, se saltan.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "prototype-svelte" / "src" / "lib" / "troughs.js"

# Onda gaussiana sobre un flujo del oeste, con Z bajando hacia el norte. El
# signo de la amplitud la convierte en vaguada o en dorsal, y `centro` la
# coloca donde haga falta: en mitad del dominio o pegada a un borde.
GUION = """
const T = await import('file://%(modulo)s');
const W = 1121, H = 717;
function onda(amplitud, centro) {
  const z = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const norte = (H - y) / H;
    const eje = (x - centro) / 170;
    z[y * W + x] = 578 - 26 * norte
      - amplitud * Math.exp(-eje * eje) * (0.35 + 0.65 * norte)
      + 1.2 * Math.sin(x / 9) * Math.cos(y / 11);
  }
  return z;
}
// Vaguada asimétrica: flanco de poniente cerrado y de levante tendido, como la
// del golfo de Bizkaia del 16 de septiembre de 2026 a H+22.
function asimetrica(amplitud, oeste, este) {
  const z = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const norte = (H - y) / H;
    const eje = (x - 560) / (x < 560 ? oeste : este);
    z[y * W + x] = 578 - 26 * norte
      - amplitud * Math.exp(-eje * eje) * (0.35 + 0.65 * norte)
      + 1.2 * Math.sin(x / 9) * Math.cos(y / 11);
  }
  return z;
}
function gotaFria() {
  const z = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const r = Math.hypot((x - 560) / 150, (y - 360) / 150);
    z[y * W + x] = 576 - 10 * ((H - y) / H) - 34 * Math.exp(-r * r);
  }
  return z;
}
function vaguadaConMinimo() {
  const z = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const norte = (H - y) / H, eje = (x - 560) / 170;
    z[y * W + x] = 578 - 26 * norte - 18 * Math.exp(-eje * eje) * (0.35 + 0.65 * norte)
      - 1.5 * Math.exp(-(((x - 560) / 60) ** 2 + ((y - 300) / 60) ** 2));
  }
  return z;
}
const minimoDeGota = () => T.troughAxes(gotaFria(), { width: W, height: H }).lows
  .map((baja) => ({ minimo: baja.minimum, grueso: baja.value }));
const resumen = (campo) => {
  const r = T.troughAxes(campo, { width: W, height: H });
  return {
    ejes: r.axes.length,
    bajas: r.lows.length,
    x: r.axes.map((eje) => Math.round(eje.reduce((s, p) => s + p.x, 0) / eje.length)),
    desvio: r.axes.map((eje) => Math.round(Math.max(...eje.map((p) => Math.abs(p.x - 560))))),
    alto: r.axes.map((eje) => Math.round(Math.max(...eje.map((p) => p.y)) - Math.min(...eje.map((p) => p.y))))
  };
};
console.log(JSON.stringify({
  centro: resumen(onda(16, 560)),
  borde_oeste: resumen(onda(16, 120)),
  borde_este: resumen(onda(16, 1000)),
  dorsal_centro: resumen(onda(-16, 560)),
  dorsal_borde: resumen(onda(-16, 120)),
  plano: resumen(onda(0, 560)),
  asimetrica: resumen(asimetrica(16, 120, 500)),
  asimetrica_espejo: resumen(asimetrica(16, 500, 120)),
  dorsal_asimetrica: resumen(asimetrica(-16, 120, 500)),
  // Isohipsa en V con el fondo plano entre las columnas 8 y 12.
  fondo_plano: (() => {
    const w = 21, h = 30, campo = new Float32Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const fondo = 20 - Math.max(0, Math.abs(x - 10) - 2);
      campo[y * w + x] = 570 + (y - fondo);
    }
    return T.troughVertex(campo, w, h, { x: 4, y: 14 }, 10);
  })(),
  // Gota fría somera: 0,9 dam de hondura. No cierra 2 dam por encima de su
  // fondo, pero la isohipsa dibujada de 576 dam la rodea.
  gota_somera: (() => {
    const w = 60, h = 60, z = new Float32Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const r2 = ((x - 30) ** 2 + (y - 30) ** 2) / 64;
      z[y * w + x] = 575.4 + 0.9 * (1 - Math.exp(-r2)) + 0.01 * (y - 30);
    }
    return [0, 4].map((paso) => T.closedLows(z, w, h, 8, 2, paso).map(({ x, y }) => [x, y]));
  })(),
  amplitud_lados: {
    simetrica: T.asymmetricAmplitude(9, 9),
    tendida: T.asymmetricAmplitude(27, 4.5),
    flanco_dorsal: T.asymmetricAmplitude(30, -3)
  },
  gota_fria: resumen(gotaFria()),
  minimo_gota: minimoDeGota(),
  vaguada_con_minimo: resumen(vaguadaConMinimo()),
  amplitud: {
    // Caso real H+32: la vaguada está bien anclada en dos isohipsas y luego
    // se debilita. La mediana sola descartaba sus 526 km de eje completo.
    anclada: T.supportsTroughAmplitude([
      { amplitude: 118.7 / 20, span: 10 },
      { amplitude: 191.8 / 20, span: 19 },
      { amplitude: -25.9 / 20, span: 17 },
      { amplitude: 28.4 / 20, span: 25 },
      { amplitude: 72.5 / 20, span: 25 },
      { amplitude: 67.4 / 20, span: 25 }
    ], 150 / 20, 25),
    // Pasada anterior junto al borde: ningún par alcanza el listón y dos
    // vértices cambian de signo, así que no debe reaparecer al abrir el caso.
    dudosa: T.supportsTroughAmplitude([
      { amplitude: 83 / 20, span: 14 },
      { amplitude: 59 / 20, span: 16 },
      { amplitude: 28 / 20, span: 18 },
      { amplitude: 15 / 20, span: 19 },
      { amplitude: -26 / 20, span: 18 },
      { amplitude: -38 / 20, span: 17 }
    ], 150 / 20, 25),
    dorsal: T.supportsTroughAmplitude([
      { amplitude: -120 / 20, span: 10 },
      { amplitude: -150 / 20, span: 15 },
      { amplitude: -187 / 20, span: 22 }
    ], 150 / 20, 25)
  }
}));
"""


@pytest.fixture(scope="module")
def deteccion() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node no está disponible")
    salida = subprocess.run(
        ["node", "--input-type=module", "-e", GUION % {"modulo": MODULO}],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT,
    )
    assert salida.returncode == 0, salida.stderr[-2000:]
    return json.loads(salida.stdout)


def test_the_same_trough_is_found_at_the_centre_and_at_the_edges(deteccion):
    """La misma vaguada tiene que salir esté donde esté.

    La comprobación de amplitud compara la isohipsa con lo que hace a 500 km a
    cada lado, y el dominio de AROME es estrecho y de borde inclinado: pegada
    al oeste, esa muestra no existe y el eje se perdía entero. La ventana se
    encoge hasta donde haya campo y el umbral se ajusta a lo que se ha podido
    medir.
    """
    for sitio, esperado in (("centro", 560), ("borde_oeste", 120), ("borde_este", 1000)):
        caso = deteccion[sitio]
        assert caso["ejes"] == 1, f"{sitio}: {caso['ejes']} ejes"
        assert abs(caso["x"][0] - esperado) < 60, f"{sitio}: eje en x={caso['x'][0]}"


def test_a_ridge_never_becomes_a_trough(deteccion):
    """Los hombros de una dorsal curvan del lado ciclónico y no son vaguadas.

    Es el falso positivo que llenaba el mapa: matemáticamente, un bulto tiene
    la cima convexa y los flancos cóncavos, así que la curvatura sola los
    señala. Lo que los descarta es que la isohipsa no baja: en el eje de una
    vaguada queda al sur por los dos lados.
    """
    assert deteccion["dorsal_centro"]["ejes"] == 0
    assert deteccion["dorsal_borde"]["ejes"] == 0
    assert deteccion["plano"]["ejes"] == 0


def test_an_asymmetric_trough_is_still_a_trough(deteccion):
    """Un flanco tendido no borra una vaguada que se ve a simple vista.

    Pedir la amplitud entera al lado menos favorable dejaba fuera las vaguadas
    con un flanco muy cerrado y el otro casi plano. Además, el pico de curvatura
    cae en el flanco cerrado y no en el fondo de la onda, así que medida desde
    ahí la isohipsa seguía bajando hacia el otro lado.
    """
    for caso in ("asimetrica", "asimetrica_espejo"):
        assert deteccion[caso]["ejes"] == 1, caso
        assert abs(deteccion[caso]["x"][0] - 560) < 40, deteccion[caso]
    assert deteccion["dorsal_asimetrica"]["ejes"] == 0
    # El eje va por el fondo de las isohipsas, no por el flanco cerrado donde
    # gira más, y se prolonga por ellas de norte a sur del dominio.
    for caso in ("asimetrica", "asimetrica_espejo", "centro"):
        assert deteccion[caso]["desvio"][0] < 40, deteccion[caso]
        assert deteccion[caso]["alto"][0] > 550, deteccion[caso]
    fondo = deteccion["fondo_plano"]
    # La isohipsa de 568 dam que pasa por el punto de partida toca fondo en la
    # fila 18 a lo largo de toda la meseta: se queda con su centro.
    assert fondo["level"] == 568
    assert fondo["x"] == 10 and abs(fondo["y"] - 18) < 0.01, fondo
    lados = deteccion["amplitud_lados"]
    assert lados["simetrica"] == 9
    assert lados["tendida"] > 7.5 * 1.0 and lados["tendida"] < 15.75
    assert lados["flanco_dorsal"] < 0


def test_a_cut_off_low_is_a_low_and_an_open_trough_is_an_axis(deteccion):
    """Baja cerrada y vaguada abierta son cosas distintas y se separan.

    Un mínimo local no basta para llamar baja a algo: una vaguada abierta
    también los tiene, y tomarlo por una depresión partía el eje justo por su
    parte más marcada. Hace falta que la isohipsa se cierre de verdad
    alrededor, sin escaparse por el borde del mapa.
    """
    assert deteccion["gota_fria"]["ejes"] == 0
    assert deteccion["gota_fria"]["bajas"] == 1
    # El valor que acompaña a la B es el mínimo del campo del modelo, no el
    # fondo más somero de la rejilla suavizada con la que se detecta.
    # En el centro, 576 - 10 · 0,498 - 34 = 537,0 dam.
    [gota] = deteccion["minimo_gota"]
    assert abs(gota["minimo"] - 537.0) < 0.05, gota
    assert gota["minimo"] < gota["grueso"], gota
    con_minimo = deteccion["vaguada_con_minimo"]
    assert con_minimo["ejes"] == 1
    assert con_minimo["bajas"] == 0


def test_a_shallow_low_closed_by_a_drawn_isohypse_is_a_closed_circulation(deteccion):
    """Una gota fría somera rodeada por una isohipsa del mapa es una baja.

    Pedirle 2 dam de cierre sobre su mínimo la dejaba pasar por vaguada, y el
    detector dibujaba un eje por el borde de una circulación cerrada (AROME 12Z
    del 15/09/2026, H+41, sobre el centro peninsular).
    """
    sin_paso, con_paso = deteccion["gota_somera"]
    assert sin_paso == []
    assert con_paso == [[30, 30]]


def test_a_trough_can_weaken_after_two_strong_consecutive_levels(deteccion):
    """Una cola somera no borra una vaguada bien anclada aguas arriba."""
    assert deteccion["amplitud"] == {
        "anclada": True,
        "dudosa": False,
        "dorsal": False,
    }


GUION_CENTROS = """
const P = await import('file://%(modulo)s');
const W = 400, H = 300;
// Una baja de 12 hPa de hondura, un anticiclón de 10 y una arruga de 1 hPa
// que no debe salir. La baja lleva encima ruido de celda, que es lo que el
// suavizado de detección tiene que ignorar.
function campo() {
  const p = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const baja = -12 * Math.exp(-(((x - 100) / 45) ** 2 + ((y - 120) / 45) ** 2));
    const alta = 10 * Math.exp(-(((x - 300) / 50) ** 2 + ((y - 170) / 50) ** 2));
    const arruga = -1 * Math.exp(-(((x - 200) / 20) ** 2 + ((y - 60) / 20) ** 2));
    p[y * W + x] = 1013 + baja + alta + arruga + 0.4 * Math.sin(x / 3) * Math.cos(y / 4);
  }
  return p;
}
const centros = P.pressureCentres(campo(), { width: W, height: H, cellKm: 2.5 });
console.log(JSON.stringify(centros.map((c) => ({
  tipo: c.type, valor: Math.round(c.value), x: Math.round(c.x), y: Math.round(c.y),
  principal: c.main, cierre: Number(c.prominence.toFixed(1)), radio: Math.round(c.radiusKm)
}))));
"""


@pytest.fixture(scope="module")
def centros() -> list:
    if shutil.which("node") is None:
        pytest.skip("node no está disponible")
    modulo = ROOT / "prototype-svelte" / "src" / "lib" / "pressureCentres.js"
    salida = subprocess.run(
        ["node", "--input-type=module", "-e", GUION_CENTROS % {"modulo": modulo}],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    assert salida.returncode == 0, salida.stderr[-2000:]
    return json.loads(salida.stdout)


def test_pressure_centres_find_the_low_and_the_high_and_ignore_the_wrinkle(centros):
    """Una baja, un anticiclón y nada más.

    En un campo de 2,5 km hay cientos de mínimos locales y casi todos son ruido
    o un valle entre montañas. Lo que separa un centro es ganarle al entorno
    por un margen de presión —2,5 hPa a 200 km— y no tener otro del mismo signo
    al lado. La arruga de 1 hPa del campo de prueba se queda fuera por eso.
    """
    assert len(centros) == 2, centros
    baja = next(c for c in centros if c["tipo"] == "low")
    alta = next(c for c in centros if c["tipo"] == "high")
    assert abs(baja["x"] - 100) < 25 and abs(baja["y"] - 120) < 25
    assert abs(alta["x"] - 300) < 25 and abs(alta["y"] - 170) < 25
    # El valor sale del campo suavizado: sin el ruido de celda, que si no haría
    # bailar la etiqueta un hectopascal de una hora a otra.
    assert 1000 <= baja["valor"] <= 1003
    assert 1021 <= alta["valor"] <= 1024


def test_a_centre_is_main_or_relative_by_how_much_it_closes(centros):
    """Mayúscula y minúscula salen del cierre, no del tamaño de la anomalía.

    Δp es lo que hay entre el centro y el collado por el que se derrama o se
    une a un sistema más intenso, medido por inundación. El anillo fijo que
    había antes comparaba contra el sector que más favorecía al candidato, así
    que un mínimo pegado a una vaguada sal\u00eda tan cerrado como una borrasca
    redonda.
    """
    for centro in centros:
        # Los dos del campo de prueba cierran de sobra y ocupan lo suyo.
        assert centro["cierre"] >= 4, centro
        assert centro["radio"] >= 150, centro
        assert centro["principal"] is True, centro
