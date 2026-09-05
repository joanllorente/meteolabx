# Auditoría científica de Forecast AROME — 5 de septiembre de 2026

**Actualización: los ocho problemas A1–A8 se han corregido en el código local.**

- CAPE/CIN integran entre los extremos interpolados. Se adopta la integral con signo entre el primer NCL y el último EL, incluidas las bolsas negativas intermedias. Sin NCL se devuelve energía cero; con techo aún flotante, CAPE se integra hasta el techo disponible y EL queda indefinido.
- EL se interpola en altura y presión; ya no se sustituye por el techo de un tramo o por el último nivel disponible.
- Las medias en presión y SRH requieren cobertura completa de la capa.
- Los porcentajes respetan sus unidades explícitas. Las bandas sin etiqueta siguen la convención porcentual de WCS AROME; las unidades desconocidas se rechazan.
- Ambos caminos de acumulación comprueban la continuidad horaria.
- DEPR deja de aceptarse como Td. Si IP3 solo aporta parte del rocío exacto, los niveles restantes se solicitan al WCS.
- VT incorpora la presión superficial y enmascara niveles enterrados. SHIP también invalida el ingrediente 700–500 cuando 700 hPa queda bajo el terreno.

La revisión científica del almacén es ahora `AROME_CALCULATION_REVISION=1`. Los productos afectados usan claves `--calc1`; al leer manifiestos antiguos, sus horas se retiran de disponibilidad y el worker puede reencolarlas. Los archivos anteriores permanecen bajo el RUN y la poda ordinaria sigue cubriéndolos. El cliente usa `forecast-fields-v19`; el frontend ha sido recompilado e instalado en los destinos locales Streamlit y web. **No se ha desplegado al servidor remoto ni ejecutado una regeneración remota de pasadas.**

Validación tras las correcciones: **229 pruebas aprobadas**, incluidas 28 regresiones de auditoría, y **7 pruebas del frontend aprobadas**. Se ha contrastado la integral con cuadratura independiente de SciPy y casos con varias capas flotantes, origen elevado, techo abierto y datos ausentes. Compilación del frontend correcta. Dos pruebas de fronteras administrativas fallaron por DNS en la ejecución completa y se excluyeron de la última ejecución; no se cambiaron para ocultar el fallo.

```bash
.venv/bin/python -m pytest tests/backend/test_forecast_scientific_audit.py -q
```

Las pruebas de auditoría ya no contienen `xfail`. El texto que sigue conserva el diagnóstico y los resultados **anteriores a las correcciones**, como registro de qué se encontró. Sus propuestas, números de línea y recuentos históricos no describen el estado corregido. Las limitaciones adicionales de validación meteorológica siguen aplicando.

---

La revisión de los 29 productos conectados identifica ocho problemas reproducibles. Los más importantes están en la integración de CAPE/CIN y en el nivel de equilibrio de las parcelas, compartidos por varios mapas derivados. Otros requieren condiciones concretas: perfiles incompletos, porcentajes muy pequeños, horas ausentes o campos isobáricos bajo el terreno.

Se ha revisado el código local, el montaje de perfiles WCS/IP1/IP3/SP1/SP2, unidades, alturas, interpolación, derivadas, almacenamiento de rejillas y representación. No se ha descargado una pasada real ni comparado todos sus puntos con un cálculo independiente. Por tanto, los ejemplos demuestran errores del algoritmo; **no cuantifican la frecuencia ni el tamaño del error en los mapas publicados**. «Sin fallo localizado» no equivale a una certificación meteorológica.

Esta entrega es una revisión: se añaden este informe y pruebas reproducibles. No se modifican las fórmulas operativas ni se regeneran mapas almacenados. El repositorio contenía cambios anteriores ajenos a la revisión.

## Problemas reproducidos

### A1. CAPE/CIN: cancelación dentro de los tramos y límite incorrecto de CIN — prioridad alta

En `server/services/convective_diagnostics.py:401`, primero se integra cada tramo completo y después se clasifica el resultado por su signo. Si la flotabilidad cambia de signo dentro del tramo, la parte positiva y la negativa se cancelan. El límite de CIN se determina con esos trapecios, aunque el NCL se interpola por otro procedimiento.

Ejemplo de cuadratura controlada: alturas `[0,1000,2000,3000,5000]` m y flotabilidades `[0,-0.2,0.2,-0.2,-0.2]` m/s². La función localiza NCL=1500 m, pero devuelve CAPE=0 J/kg y CIN=−500 J/kg. Integrando la función lineal por tramos hasta los cruces se obtiene CAPE=100 J/kg y CIN=−150 J/kg. La prueba sustituye únicamente la termodinámica por flotabilidad conocida para aislar el fallo matemático.

Afecta directamente a MUCAPE, MLCAPE y SBCAPE. El error de CAPE/CIN puede cambiar qué parcela cumple los umbrales de base efectiva y, por tanto, EBWD. SHIP hereda la MUCAPE. Los LI se calculan por diferencia de temperatura a 500 hPa y no heredan directamente este fallo de integración.

Corrección propuesta: interpolar los cruces, limitar la integral al NCL/EL y definir explícitamente el tratamiento de múltiples capas de flotabilidad. Como referencia independiente, [MetPy documenta CIN hasta el LFC y CAPE entre LFC y EL, interpolando intersecciones](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.cape_cin.html). No basta cambiar `maximum` o el signo del trapecio.

### A2. Nivel de equilibrio (EL) asignado a un nivel entero — prioridad alta

En `server/services/convective_diagnostics.py:420`, EL es el techo del último tramo cuya integral es positiva, sin interpolar el cruce descendente de flotabilidad. Una transición de +0.2 a −0.1 m/s² entre 2000 y 3000 m tiene EL=2666.67 m; el código devuelve 3000 m. Con otros signos puede quedarse por debajo o no encontrarlo. Si el perfil termina aún flotante, también confunde el techo disponible con un EL observado.

Afecta a la altura objetivo de EBWD y al límite superior del viento medio de célula ordinaria. Corrección: interpolar el último cruce válido y distinguir «EL encontrado» de «perfil truncado aún flotante». Debe revisarse conjuntamente con A1.

### A3. Medias en presión de una capa incompleta — prioridad media

En `server/services/convective_diagnostics.py:618`, los tramos ausentes se omiten de la integral, pero el denominador sigue siendo la profundidad total solicitada. Un viento constante de 10 m/s conocido solo entre 1000 y 800 hPa da **5 m/s** al pedir la media 1000–600 hPa. También se reproduce con un hueco interior. No existe información para afirmar la media de la capa completa.

Afecta al movimiento de célula ordinaria y a la construcción de la parcela ML100. En termodinámica completa y viento incompleto puede producir movimiento falsamente lento. Corrección: comprobar que la suma de espesores válidos cubre toda la capa; en caso contrario, devolver NaN. Dividir solo por el espesor disponible tampoco representa la capa anunciada.

### A4. SRH acepta capas incompletas — prioridad menor por protección del llamador

En `server/services/convective_diagnostics.py:1127`, SRH suma los aportes finitos y basta un tramo válido para devolver un número. Un perfil de solo 1 km da 100 m²/s² al solicitar SRH 0–3 km; otro con hueco interior da 125 m²/s².

**Alcance importante:** en el flujo actual el movimiento de Bunkers normalmente resulta NaN si falta viento dentro de 0–6 km, lo que a su vez invalida SRH. Esta protección reduce el impacto operativo del defecto de la función aislada. No se ha demostrado que los mapas actuales estén publicando esos números parciales. Conviene exigir cobertura completa también dentro de SRH para que su contrato no dependa del llamador. El signo en perfiles completos sí está contrastado con MetPy por las pruebas existentes.

### A5. Un porcentaje pequeño se transforma en fracción — prioridad media

En `server/services/arome_forecast.py:2087`, humedad relativa y nubosidad se multiplican por 100 si el máximo absoluto del campo no supera 1.5, ignorando las unidades explícitas. Un campo con valor 1 y unidad `%` termina en **100 %**.

La condición afecta a todo el campo leído, no a una celda aislada dentro de un mapa húmedo o nuboso. Es más fácil activarla en un recorte muy seco o casi despejado; no se ha medido su frecuencia real. Corrección: dar precedencia a las unidades y utilizar una convención documentada del proveedor cuando falten, sin decidir por los valores meteorológicos.

### A6. Precipitación acumulada con horas ausentes — prioridad media, condicional

En `server/services/arome_forecast.py:2257`, se suman las horas presentes en el catálogo sin comprobar continuidad desde el RUN. Con H+1 y H+3 disponibles, de 1 mm cada una, se publica 2 mm como acumulado hasta H+3 aunque H+2 sea desconocida. El camino individual de `_computed_frame` contiene la misma suposición.

Las pruebas existentes verifican que ambos caminos coinciden con un catálogo completo; no validaban huecos temporales. No se ha observado un catálogo real con ese hueco durante esta revisión. Corrección: verificar el conjunto horario completo y diferir la publicación o marcar ausencia. No debe tratarse una hora ausente como lluvia nula.

### A7. DEPR admitido como punto de rocío — prioridad media, latente

En `server/services/arome_packages.py:61`, `DEPR` se incluye entre los alias de punto de rocío. Sin embargo, [la tabla oficial GRIB2 de NOAA distingue DPT (Td) de DEPR (T−Td)](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-0.shtml). El lector devuelve DEPR=5 como rocío=5 y el adaptador lo etiqueta en °C.

Ejemplo: T=20 °C y DEPR=5 K implican Td=15 °C, no 5 °C. Afectaría principalmente a DCAPE, que solicita el rocío exacto de IP3; las otras parcelas usan normalmente RH de IP1. No se ha demostrado que IP3 esté enviando DEPR en producción: el fallo está en un alias admitido. Corrección: eliminar ese alias o convertirlo explícitamente usando T y sus unidades.

### A8. Vertical Totals utiliza niveles subterráneos — prioridad media

En `server/services/arome_forecast.py:1892`, se calcula correctamente T850−T500, pero no se comprueba la presión superficial. Con superficie a 800 hPa, devuelve VT=30 °C para T850=10 °C y T500=−20 °C, aunque 850 hPa queda bajo el suelo. Se reproduce en el camino WCS y la lógica de paquetes tampoco introduce máscara.

El valor puede ser una extrapolación del modelo, pero no describe una capa atmosférica real sobre esa montaña. El mapa de theta-e850 ya aplica esa protección. Corrección: enmascarar donde la presión superficial no permita ambos niveles. Revisar la misma política en T850 y RH700, y especialmente en SHIP: el montaje sustituye niveles enterrados por superficie y luego su ingrediente «gradiente 700–500» usa esas posiciones sin verificar que 700 hPa esté sobre el suelo.

## Cobertura de los 29 productos

| Productos | Resultado de la revisión |
| --- | --- |
| T2m | Conversión K/°C y selección del nivel coherentes para las unidades previstas. Sin fallo localizado. |
| T850/Z850, T500/Z500 | Conversiones a °C y dam coherentes. T850 puede mostrar extrapolación bajo terreno; debe explicitarse o enmascararse. |
| Cizalladura 0–1, 0–3 km | Diferencia vectorial U/V y alineación coherentes; base real a 10 m, como dice la guía. |
| Cizalladura 0–6 km | Interpola viento a terreno+6000 m en alturas absolutas; no hay doble suma. Si falta terreno, el fallback cero sí puede convertirla en 6 km sobre el mar. |
| EBWD | Mitad de la profundidad desde base efectiva a EL y referencias de altura coherentes. Hereda A1/A2. |
| SHIP | Fórmula y adaptador contrastados con SHARPpy en pruebas existentes. Hereda A1; revisar ingrediente 700 hPa enterrado y nivel de congelación en perfiles fríos. |
| MUCAPE/MULI, MLCAPE/MLLI, SBCAPE/SBLI | A1/A2 en parcelas; A3 en ML incompleta. LI tiene signo T entorno−T parcela correcto a 500 hPa. |
| DCAPE | Procedimiento de descenso y prueba de referencia SHARPpy aprobados para el perfil ensayado. A7 latente; no equivale a validación de todas las situaciones. |
| UH 2–5 km | Corrección del signo latitudinal presente. Halos de bandas y rechazo de huecos internos pasan pruebas. Ver limitaciones de coordenadas abajo. |
| w en NCL | NCL se convierte a AGL en `diagnose_convection`; se suma terreno una vez para interpolar en alturas absolutas. Corrección anterior comprobada, incluido terreno elevado. |
| SRH 0–1, 0–3 km | Signo e integración en perfiles completos contrastados con MetPy. A4 en función aislada, normalmente protegido por Bunkers. |
| Movimiento de célula ordinaria | Media ponderada por presión entre LCL y EL de ML coherente con la definición local; A2/A3. |
| MU-ECAPE, ML-ECAPE | Se transmiten como campos nativos; no usan el integrador de CAPE propio. No se ha auditado el algoritmo interno de Météo-France ni confirmado independientemente la etiqueta de arrastre. |
| Reflectividad | Lectura nativa y recorte a 0 dBZ. El recorte es una decisión de visualización que elimina ecos negativos; no un cambio de signo. |
| MSLP/theta-e850 | MetPy para theta-e, conversión K→°C, Pa→hPa y máscara bajo terreno coherentes. |
| Precipitación 1 h | Selección explícita PT1H y lectura del incremento coherentes. |
| Precipitación acumulada | Suma de incrementos H+1…H+n coherente con catálogo completo; A6. Reutiliza incrementos cuantizados, introduciendo redondeo acumulativo. |
| Racha máxima 10 m | Campo nativo y PT1H coherentes; no se calcula como módulo de componentes medias. |
| RH700, nubosidad total | A5. RH700 tampoco filtra niveles subterráneos. |
| Radiación solar descendente | J/m² horarios divididos por 3600 dan W/m² correctamente. Debe contrastarse con metadatos de un mensaje real: el código aplica el factor fijo incluso si una respuesta futura fuera flujo medio. |
| Vertical Totals | Diferencia y normalización de unidades verificadas; A8. |

## Limitaciones adicionales y comprobaciones necesarias

- **Termodinámica aproximada:** la función propia de theta-e reconoce que mezcla variantes de Bolton. El mapa theta-e usa MetPy; las parcelas propias no. No se ha atribuido a esa aproximación un error operativo cuantificado. Tras resolver A1/A2, contrastar perfiles marítimos, secos, elevados, con inversión, varias capas flotantes y EL por encima del techo disponible.
- **Geometría de UH:** las derivadas se toman entre vecinos de superficies isobáricas, no remuestreados a altura constante. El signo corregido no demuestra equivalencia exacta con vorticidad vertical geométrica; habría que contrastar el efecto de la pendiente de las superficies y la cizalladura. No se cuenta como fallo reproducido en esta entrega.
- **Nivel de congelación:** `freezing_level_m` busca el primer cruce cálido→frío. Una columna completamente bajo cero devuelve NaN, propagado a SHIP. Hace falta definir el comportamiento deseado para suelo frío y capas cálidas elevadas; no convertir indiscriminadamente toda ausencia de cruce en cero.
- **Vaguadas y dibujo de viento:** el detector utiliza una celda isotrópica de 2.5 km y no recibe latitud; la rejilla 0.025° tiene unos 2.78 km norte–sur y 1.58–2.21 km este–oeste en este dominio. Sus longitudes/curvaturas son aproximadas. Las líneas de corriente avanzan por índices usando U/V sin transformar m/s a desplazamientos geográficos. Son limitaciones de representación/análisis auxiliar, distintas de invertir el signo del campo numérico.
- **Persistencia:** corregir una fórmula no reemplaza automáticamente los mapas precalculados. Al implementar correcciones habrá que regenerar los productos afectados y revisar la revisión de caché del cliente. Esta auditoría no ha borrado ni recalculado datos publicados.

La [documentación de Météo-France](https://donneespubliques.meteofrance.fr/client/document/doc_arome_pour-portail_20250806_410.pdf) respalda los niveles y la rejilla usados como referencia. No sustituye comprobar las etiquetas y unidades de los mensajes realmente descargados.

## Evidencia ejecutable

Archivo nuevo: `tests/backend/test_forecast_scientific_audit.py`.

```bash
.venv/bin/python -m pytest tests/backend/test_forecast_scientific_audit.py -q -rx
.venv/bin/python -m pytest tests/backend/test_forecast_scientific_audit.py --runxfail -q --tb=short
```

Resultado: **2 pruebas correctas y 12 fallos esperados**, correspondientes a A1–A8. Con `--runxfail` se observan los 12 fallos numéricos explícitos. Las marcas `xfail(strict=True)` documentan defectos pendientes, no los consideran resueltos; deben retirarse al corregirlos.

Suite previa ejecutada: `test_convective_diagnostics.py`, `test_arome_packages.py`, `test_forecast_endpoint.py`, `test_forecast_worker.py`, `test_forecast_store.py`: **184 aprobadas y 2 fallidas por resolución DNS de mapas.fomento.gob.es**, ambas de caché de fronteras administrativas. No son fallos meteorológicos. Contornos y centros de presión del frontend: **7 pruebas aprobadas**.

Orden recomendado de corrección: A1/A2 conjuntamente por su alcance científico; A5/A7 por conversión de magnitudes; A3/A8/A6 por integridad de los datos; A4 como refuerzo del contrato. Después, validación con una muestra de perfiles reales y regeneración controlada de los mapas afectados.
