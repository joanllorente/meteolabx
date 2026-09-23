# Diagnóstico de la memoria de la API

Cada mantenimiento (15 minutos) registra `allocator_samples` en MiB, con tres
muestras: antes de limpiar, después de limpiar y después del diagnóstico Python.
Las diferencias incluyen las peticiones concurrentes: no son una medida aislada
del coste de `tracemalloc`.

| Campo | Interpretación |
| --- | --- |
| `arenas` | Áreas que declara glibc; permite evaluar `MALLOC_ARENA_MAX`. |
| `arena_reserved_mib` | Espacio obtenido por las áreas; no equivale a RSS. |
| `arena_free_bins_mib` | Bloques libres declarados por glibc. No garantiza que sean residentes ni que puedan devolverse íntegramente. |
| `arena_not_in_free_bins_mib` | Reserva menos bloques libres. Incluye metadatos y cachés de hilos; no equivale a objetos vivos. |
| `malloc_mmap_mib` | Asignaciones mmap declaradas por glibc; no todos los mmap del proceso. |
| `Rss_mib` / `Anonymous_mib` | Memoria residente total/anónima del proceso según Linux. |
| `AnonHugePages_mib` | Parte anónima en páginas grandes; está incluida en las anteriores. |
| `trace.current_mib` / `metadata_mib` | Asignaciones rastreadas y metadatos internos de tracemalloc. |

No sumar estos grupos: son vistas solapadas de la memoria. `malloc_info` incluye
las áreas de glibc, pero no desglosa los objetos Python, los pools de pymalloc o
mmap directos de otras bibliotecas. La memoria creada antes de iniciar el trazado
puede no figurar en sus contadores.

El registro ligero funciona sin activar tracemalloc y no recorre los objetos ni
crea nuevas instantáneas. En sistemas sin las interfaces necesarias registra
`unavailable`, nunca cero como sustituto de un dato desconocido.

Si crecen los bloques libres tras la carga, puede haber retención del asignador.
Si crece la memoria rastreada, revisar las ubicaciones que siguen reteniendo
objetos. Si la RSS sube tras el diagnóstico con poca actividad concurrente,
medir el coste de las instantáneas y comparar una ventana sin trazado. Ninguno de
estos casos por sí solo demuestra una fuga.

Referencia: [malloc_info, Linux man-pages](https://man7.org/linux/man-pages/man3/malloc_info.3.html).
