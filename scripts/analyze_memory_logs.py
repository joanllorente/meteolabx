#!/usr/bin/env python3
"""Resumen de memoria a partir de los logs del worker AROME.

El worker ya publica dos series que bastan para saber en qué se va la factura
de memoria de Railway, pero repartidas por miles de líneas:

  * el cgroup en cada lanzamiento — «memoria 5.3/8 GB (+1.2 recuperables)»,
    donde la primera cifra es la anónima, que es la que provoca el OOM;
  * el pico de cada perfil convectivo — «pico 2.8 GB», el `ru_maxrss` del
    proceso aislado.

Esto las junta y las resume. La lectura útil no es el máximo —ese ya se ve en
la gráfica del proveedor— sino el VALLE: si la memoria baja entre ciclos, el
gasto es trabajo real y se regula con el paralelismo; si no baja, algo se
queda retenido y el paralelismo no es la palanca.

    railway logs --service <python> | python scripts/analyze_memory_logs.py
    python scripts/analyze_memory_logs.py forecast.log
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

# «memoria 5.3/8 GB» y, si el page cache suma, «(+1.2 recuperables)».
MEMORIA = re.compile(
    r"memoria\s+(?P<anon>[\d.]+)/(?P<limite>[\d.]+)\s*GB"
    r"(?:\s*\(\+(?P<cache>[\d.]+)\s+recuperables\))?"
)
PICO = re.compile(r"pico\s+(?P<gb>[\d.]+)\s*GB")
ESPERA = re.compile(r"Perfil de nivel (?P<tier>\d+) en espera")
COMPLETADO = re.compile(r"Completado .*: \+(?P<frames>\d+) frames")
# Railway antepone la marca de tiempo a cada línea.
SELLO = re.compile(r"(?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")


def _describe(nombre: str, valores: list[float], unidad: str = "GB") -> str:
    if not valores:
        return f"{nombre}: sin muestras"
    ordenados = sorted(valores)
    p95 = ordenados[min(len(ordenados) - 1, int(len(ordenados) * 0.95))]
    return (
        f"{nombre}: n={len(valores)} · mín {ordenados[0]:.1f} · "
        f"mediana {statistics.median(ordenados):.1f} · p95 {p95:.1f} · "
        f"máx {ordenados[-1]:.1f} {unidad}"
    )


def analizar(lineas) -> int:
    anon: list[float] = []
    cache: list[float] = []
    picos: list[float] = []
    limite = 0.0
    esperas = 0
    frames = 0
    primera = ultima = None

    for linea in lineas:
        sello = SELLO.search(linea)
        if sello:
            primera = primera or sello.group("iso")
            ultima = sello.group("iso")
        if m := MEMORIA.search(linea):
            anon.append(float(m.group("anon")))
            limite = max(limite, float(m.group("limite")))
            cache.append(float(m.group("cache") or 0.0))
        if m := PICO.search(linea):
            picos.append(float(m.group("gb")))
        if ESPERA.search(linea):
            esperas += 1
        if m := COMPLETADO.search(linea):
            frames += int(m.group("frames"))

    if not anon and not picos:
        print(
            "Ninguna línea reconocida. ¿Es el log del servicio Python y "
            "abarca un ciclo del worker?",
            file=sys.stderr,
        )
        return 1

    if primera:
        print(f"Ventana: {primera} → {ultima}")
    if limite:
        print(f"Límite del contenedor: {limite:.0f} GB")
    print(_describe("Memoria anónima (la que mata)", anon))
    print(_describe("Caché recuperable (lo que infla la gráfica)", cache))
    print(_describe("Pico por perfil", picos))
    print(f"Perfiles pesados aplazados por el freno: {esperas}")
    print(f"Frames completados en la ventana: {frames}")

    if anon:
        valle, cima = min(anon), max(anon)
        print()
        # El diagnóstico es la única conclusión que no se lee de un número
        # suelto: hace falta comparar el valle con la cima.
        if limite and cima > limite * 0.9:
            print(
                "⚠︎  La anónima roza el límite: el OOM está cerca y el "
                "reinicio se disfraza de lentitud. Baja "
                "METEOLABX_FORECAST_WORKERS."
            )
        if valle > cima * 0.7:
            print(
                "⚠︎  El valle no baja: la memoria se queda retenida entre "
                "tareas. Menos paralelismo no abaratará la factura; hay que "
                "mirar qué sobrevive a cada trabajo."
            )
        else:
            print(
                "✓  El valle baja bien: el gasto es trabajo real, no "
                "retención. La palanca es METEOLABX_FORECAST_WORKERS "
                "(menos paralelismo = menos GB·hora, RUN más lento)."
            )
        if picos and limite:
            caben = int(limite / max(picos))
            print(
                f"✓  Con el pico máximo observado ({max(picos):.1f} GB) caben "
                f"{caben} perfiles a la vez en {limite:.0f} GB."
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log",
        nargs="*",
        type=Path,
        help="Ficheros de log. Sin argumentos, lee de la entrada estándar.",
    )
    args = parser.parse_args()
    if args.log:
        lineas = []
        for ruta in args.log:
            lineas.extend(ruta.read_text(errors="replace").splitlines())
        return analizar(lineas)
    return analizar(sys.stdin)


if __name__ == "__main__":
    raise SystemExit(main())
