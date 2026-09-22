# Planificador continuo de AROME

En `--watch`, el catálogo se consulta en un hilo independiente. El presupuesto
`METEOLABX_FORECAST_WORKER_CYCLE_BUDGET_S` solicita una renovación sin vaciar
los procesos activos. `max_tasks` limita las admisiones entre renovaciones:
al llegar al límite se solicita otro catálogo y se renueva la cuota. Si falla
la consulta se conserva la cola conocida y se reintenta después del intervalo.
Una ejecución sin `--watch` conserva los límites de salida anteriores.

El padre mantiene un único objeto de manifiesto por pasada. La renovación
fusiona horas del catálogo en ese objeto; los resultados de trabajos anteriores
actualizan la misma instancia. Se excluyen trabajos activos por los frames que
cubren, también cuando cambia la agrupación del acumulado. Los nuevos turnos se
publican inmediatamente, pero no se borran pasadas ni paquetes con trabajos o
precargas activos.

La cola conserva la prioridad por pasada y nivel y escoge el primer trabajo
**admisible**:

- Nativos y derivados rápidos pueden salir directamente.
- Perfiles requieren IP1 e IP3; IP3 aporta la velocidad vertical. Si faltan,
  esperan fuera de los procesos de cálculo. Pasados 180 s desde la primera
  aparición de la hora en el catálogo, pueden usar WCS.
- DCAPE espera IP1 e IP3. Con paquetes desactivados se mantiene la vía WCS.
- Una descarga cuyo `.part` avanza aplaza el desvío WCS. Un parcial sin avance
  deja de aplazarlo después de 60 s, incluso si quedó huérfano.
- Solo se admite un perfil/DCAPE por WCS a la vez. Las lecturas nativas y de
  superficie siguen compartiendo el limitador global de peticiones.

Los trabajos pesados se cuentan juntos entre niveles. Se conserva el límite
`heavy_workers`, el intervalo de 15 s cuando hay otro pesado activo y la
admisión por memoria anónima libre. La reserva de 3 GiB no se ha reducido.

En los hijos del planificador, la lectura de paquetes no inicia transferencias
ni repite los 180 s: toma el paquete listo o sigue una descarga que ya tenga el
cerrojo mientras progrese. La precarga mantiene su propio ciclo de vida, con
cuatro transferencias por defecto, y ECMWF se ejecuta en un hilo independiente.
SIGTERM detiene nuevas admisiones y espera a los cálculos activos, respetando
sus timeouts, y al ciclo ECMWF actual. Las descargas anticipadas conservan el
comportamiento de parada existente: señal de parada y espera breve, con sus
archivos incompletos aislados como `.part`.

## Validación y medición

Las pruebas simulan catálogos lentos mientras continúan los cálculos, renovación
de cuota, terminaciones posteriores a la renovación, solapamientos de trabajos,
selección entre niveles, frenos de memoria, progresos y paradas de descargas y
SIGTERM. No son una medida de la mejora de rendimiento en Railway.

En Linux, cada perfil/DCAPE registra `pico_anon_muestreado_MB`, máximo de RssAnon
del proceso hijo muestreado cada 0,5 s. No incluye page cache ni es un máximo
exacto: puede perder picos entre muestras. Comparar esos valores con el pico de
memoria anónima del cgroup y varios RUN antes de ajustar la reserva o los hilos.
El resumen del planificador cada 30 s muestra activos, pendientes y si hay una
consulta al catálogo en curso; no mide por sí solo segundos de CPU desperdiciados.
