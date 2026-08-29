"""
Comportamiento del detector de vaguadas, no su estructura.

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
const resumen = (campo) => {
  const r = T.troughAxes(campo, { width: W, height: H });
  return {
    ejes: r.axes.length,
    bajas: r.lows.length,
    x: r.axes.map((eje) => Math.round(eje.reduce((s, p) => s + p.x, 0) / eje.length))
  };
};
console.log(JSON.stringify({
  centro: resumen(onda(16, 560)),
  borde_oeste: resumen(onda(16, 120)),
  borde_este: resumen(onda(16, 1000)),
  dorsal_centro: resumen(onda(-16, 560)),
  dorsal_borde: resumen(onda(-16, 120)),
  plano: resumen(onda(0, 560)),
  gota_fria: resumen(gotaFria()),
  vaguada_con_minimo: resumen(vaguadaConMinimo())
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


def test_a_cut_off_low_is_a_low_and_an_open_trough_is_an_axis(deteccion):
    """Baja cerrada y vaguada abierta son cosas distintas y se separan.

    Un mínimo local no basta para llamar baja a algo: una vaguada abierta
    también los tiene, y tomarlo por una depresión partía el eje justo por su
    parte más marcada. Hace falta que la isohipsa se cierre de verdad
    alrededor, sin escaparse por el borde del mapa.
    """
    assert deteccion["gota_fria"] == {"ejes": 0, "bajas": 1, "x": []}
    con_minimo = deteccion["vaguada_con_minimo"]
    assert con_minimo["ejes"] == 1
    assert con_minimo["bajas"] == 0
