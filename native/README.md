# Núcleo opcional de DCAPE en C++

La extensión mueve a C++ la selección de la capa de menor theta-e y la
interpolación del estado inicial de la parcela. Trabaja columna a columna:
no construye matrices de 21 presiones objetivo por cada capa candidata.
El descenso y la integral siguen usando el código Python/SHARPpy existente.
No se han migrado los demás diagnósticos convectivos.

## Compilar y probar

Desde la raíz del proyecto, con el mismo Python que ejecutará el worker:

```sh
python -m pip install 'pybind11>=3.0,<4'
python scripts/build_dcape_native.py
python -m pytest tests/backend/test_dcape_native.py
python scripts/benchmark_dcape_native.py --rows 128 --cols 1121
```

Requiere un compilador C++17 y las cabeceras de Python. El binario generado
es específico del sistema y versión de Python; no se incluye en Git.
No se usa fast-math y se desactiva la contracción de operaciones FP.

## Selección

- `METEOLABX_DCAPE_ENGINE=python`: referencia, valor por defecto.
- `METEOLABX_DCAPE_ENGINE=cpp`: núcleo nativo; falla explícitamente si no
  se compiló, evitando un benchmark que vuelva silenciosamente a Python.

En Railway el `buildCommand` de `railway.toml` ya compila la extensión en cada
build, y `pybind11` viaja en `requirements.txt`. Eso no activa nada: el motor
por defecto sigue siendo `python`. Antes de poner `METEOLABX_DCAPE_ENGINE=cpp`
en el servicio `meteolabx` —el del worker, donde se paga la memoria— conviene
comprobar en el log del build que el `.so` se generó y validar contra perfiles
reales. Volver a `python` desactiva el núcleo sin eliminar datos.

Si la imagen del builder no trae compilador C++ o las cabeceras de Python, el
build falla ahí, de forma visible. Es preferible a desplegar sin el binario:
con el motor en `cpp` y sin `.so`, el cálculo aborta en vez de caer a Python.

El núcleo libera el GIL y no crea hilos: reutiliza los hilos de bandas del
worker, evitando multiplicar el paralelismo. Acepta vistas float32 y float64 mezcladas, con strides
(incluidos strides negativos); no fuerza copias contiguas. La envoltura Python
conserva float32 en el pase exclusivo de DCAPE nativo; el núcleo promociona
y recorta el rocío dentro de cada columna. La altura sigue calculándose en
Python con temporales float64. Los demás diagnósticos mantienen sus conversiones.

## Límites de validación

Las pruebas comparan la capa de origen, su estado y el DCAPE final; cubren
NaN, niveles repetidos, presión inválida y vistas no contiguas. El benchmark
usa procesos separados y semilla fija, comparando arrays completos y RSS
máximo del proceso. Los datos son sintéticos: no permite prometer la misma
aceleración del RUN completo. Las diferencias de redondeo entre NumPy y libm
pueden importar para capas con theta-e casi empatada; validar datos reales.

## Descenso independiente de las bandas (experimental)

`METEOLABX_DCAPE_ENGINE=cpp-column` añade bulbo húmedo y wetlift en C++
con satlift escalar por celda. La integral por niveles continúa en Python.
`cpp` conserva el descenso vectorial anterior para poder comparar.
La referencia de este modo es SHARPpy escalar con `conv=0.1`, no el
resultado del array completo: este último depende del criterio global
`abs(min(eor))`. No se promete igualdad bit a bit entre ambos modos ni se
considera el resultado escalar una validación meteorológica independiente.
Las funciones adaptadas conservan su licencia en SHARPpy-LICENSE.rst.

Se limitan las iteraciones a 100: la falta de convergencia genera un error
explícito; entradas no finitas producen NaN. Las pruebas contrastan 500
combinaciones con SHARPpy escalar, DCAPE con un descenso de referencia escalar
y una rejilla 128x384 entera frente a bandas 16/32/64 (igualdad exacta).
El tamaño predeterminado sigue siendo 128. Antes de activar 64 en producción
hay que comparar perfiles AROME reales, cuantificar la diferencia respecto
al modo anterior y medir RSS con el mismo número de workers e hilos.

## Lectura directa y reutilización por columna

El núcleo acepta vistas nativas float32/float64, incluso no contiguas o
mezcladas. El descenso Python promociona únicamente la temperatura del nivel
actual para mantener la aritmética anterior. No convierte vientos en el pase
exclusivo de DCAPE. La altura hipsométrica aún necesita temporales float64;
esta mejora no elimina todas las promociones del cálculo completo.

La interpolación avanza entre objetivos decrecientes en perfiles de presión
no creciente. Para perfiles irregulares usa el barrido original. Se memoiza
theta-e por presión exacta (sin redondear claves), sólo si existen niveles
con objetivos compartidos; los resultados NaN también se conservan.

Benchmark reproducible con datos generados fuera de los procesos medidos:

```sh
python scripts/benchmark_dcape_native.py --rows 128 --cols 1121 --dtype float32 --shared-targets
```

Una ejecución local: Python 8.35 s / 584.6 MB RSS, C++ 2.90 s / 155.1 MB,
con diferencia máxima cero. Las presiones sintéticas espaciadas 25 hPa
favorecen la reutilización: no son perfiles AROME reales. Véase
benchmark-optimized-float32.json. No comparar directamente con el antiguo
benchmark de 266 MB: cambiaron las entradas y se excluyó su generación.

## Inversor de temperatura saturada (experimental)

`METEOLABX_SATURATION_ENGINE=cpp` sustituye únicamente el Newton de la
parcela, independientemente del motor DCAPE. El valor por defecto es python.
Conserva tolerancia de residuo logarítmico 1e-7, máximo nueve iteraciones,
paso de derivada 0.08 K y límites 170–380 K. La implementación Python actual
ya excluye puntos convergidos; el beneficio potencial nativo es evitar los
arrays e índices temporales. No se ha medido aún el beneficio sobre un RUN.
Pruebas con broadcasting y entradas inválidas comparan ambos inversores
con tolerancia absoluta de 1e-8 K; también se ha ejecutado la suite convectiva
con el inversor C++ activo. Ninguna de estas variables se activa en Railway
por estos cambios.

### Rejilla de presión del benchmark

El benchmark usa por defecto 25 niveles múltiplos de 25 hPa (1000 a 400),
para representar la separación que permite reutilizar objetivos en AROME.
Las temperaturas, rocíos y presión superficial siguen siendo sintéticos:
no es una captura completa de los niveles ni de los perfiles de producción,
que el worker obtiene de los metadatos del paquete/WCS.
El JSON registra la rejilla y sus niveles para distinguir las mediciones.
`--pressure-grid irregular` conserva el antiguo linspace 1000–300 como
caso de control; `--shared-targets` sigue funcionando como alias del defecto.

## SHIP (experimental)

`METEOLABX_SHIP_ENGINE=cpp` activa SHIP nativo; por defecto continúa Python.
Se compila en la misma extensión con `scripts/build_dcape_native.py`.
La traducción conserva el adaptador de SHARPpy completo: razón de mezcla
→ rocío → razón de mezcla, cizalladura → nudos → m/s, límites y reducciones.
Usar directamente la razón de mezcla original no da el mismo resultado.
El código adaptado queda sujeto a SHARPpy-LICENSE.rst.

Medición directa sintética sobre 384×1121, 430464 celdas válidas:
Python 17.08 s, C++ 0.0280 s (~609×), diferencia máxima 4.3e-14.
Es tiempo de SHIP aislado, no una aceleración del RUN completo. La cifra
anterior de 1–2 s en el comentario no representaba este caso y se ha retirado.
Resultado: benchmark-ship-local.json. Pruebas con float32/64, broadcasting,
vistas invertidas, NaN/inf y umbrales exigen rtol/atol 1e-12.

```sh
python scripts/benchmark_ship_native.py
python scripts/benchmark_ship_native.py --inputs /ruta/ship-real.npz
```

El NPZ debe contener las seis entradas del mismo campo y hora:
`mucape`, `mu_mixing_ratio_gkg`, `lapse_rate_700_500_ckm`,
`temperature_500_c`, `shear_surface_6km_ms`, `freezing_level_agl_m`.
El benchmark informa de celdas válidas, comprueba los resultados y excluye
la carga/generación de entradas del tiempo. Falta ejecutar este ensayo
con entradas AROME reales antes de decidir la activación en producción.

## Parcela completa por columna (experimental)

`METEOLABX_PARCEL_ENGINE=cpp` sustituye `parcel_diagnostics`; por defecto
continúa Python. `native/parcel.hpp` calcula el ascenso seco/saturado,
flotabilidad virtual, LCL/LFC, CAPE/CIN y último EL por columna. Mantiene
el criterio Newton existente, bolsas negativas, perfiles incompletos y
el índice de 500 hPa seleccionado en la primera columna como Python.
No modifica el criterio del descenso DCAPE ni depende de SATURATION_ENGINE.

El núcleo acepta float32/float64 con strides, libera el GIL durante el
bucle y usa cuatro vectores de longitud igual al número de niveles.
Devuelve ocho arrays 2D float64. Los orígenes se promueven sólo en 2D.
No crea hilos ni matrices 3D auxiliares. El recorrido completo exterior
sigue promoviendo perfiles en `_convective_outputs` y `diagnose_convection`;
MU/ML, alturas y otros diagnósticos aún tienen temporales NumPy.

Pruebas: referencia Python para float32/64, bandas, vistas invertidas,
parcelas elevadas, perfiles estables, sobresaturación, niveles duplicados,
datos ausentes y llamadas concurrentes. Tolerancias por salida: rtol 1e-9,
atol 1e-6. La suite convectiva también se ejecutó con el motor nativo activo.

```sh
python scripts/build_dcape_native.py
python -m pytest tests/backend/test_parcel_native.py
METEOLABX_PARCEL_ENGINE=cpp python -m pytest tests/backend/test_convective_diagnostics.py
python scripts/benchmark_parcel_native.py
python scripts/benchmark_parcel_native.py --stage all
```

Mediciones locales sintéticas, 29×128×1121, procesos separados y entradas
float32 generadas fuera del proceso medido:

| Etapa | Pico RSS Python | Pico RSS C++ | Tiempo Python | Tiempo C++ |
|---|---:|---:|---:|---:|
| Una parcela | 979.6 MB | 117.0 MB | 1.779 s | 1.602 s |
| Diagnósticos sin DCAPE | 1760.0 MB | 984.6 MB | 8.422 s | 7.565 s |

En este ensayo las salidas comparadas coinciden exactamente. Los JSON
benchmark-parcel-local.json y benchmark-parcel-all-local.json conservan
los resultados. El ensayo completo usa SHIP C++ en ambos procesos y viento
sintético constante; no es una medición de un RUN ni de datos AROME reales.
`--inputs DIR` permite cargar p.npy/t.npy/d.npy/h.npy para la parcela;
el modo all recalcula las alturas y mantiene viento sintético.

El ahorro del núcleo no permite dar por alcanzados 280 MB para el conjunto.
No se han cambiado workers, hilos, filas por banda ni mantenimiento de memoria.
La fragmentación de glibc requiere validación en Linux; el RSS de macOS no
justifica retirar malloc_trim ni garantiza el ahorro facturable en Railway.
