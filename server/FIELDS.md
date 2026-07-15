# Procedencia de campos del contrato de observación

Referencia de qué campos de `/v1/observations/*` son **nativos** (los
reporta el proveedor), **derivados** (los calcula el backend con fórmula
determinista) o **estimados** (heurística/escala defensiva). Cuando un
campo es nativo solo para algunos proveedores, se indica.

## Observación actual (`CurrentObservation`)

| Campo | Tipo | Detalle |
|---|---|---|
| `Tc`, `RH` | nativo | Todos los proveedores. Meteohub y Windy convierten Kelvin→°C; WU/WeatherLink °F→°C. |
| `p_abs_hpa` | nativo / derivado | Nativo en AEMET (`pres`), Meteocat, MeteoGalicia, Frost, Meteohub, POEM y Windy. Derivado de la MSL (`p / e^(z/8000)`) en WU, NWS*, Met Office, Météo-France*, WeatherLink. *cuando el feed no trae la absoluta. |
| `p_hpa` (MSL) | nativo / derivado | Nativo en WU, AEMET (`pres_nmar`), NWS (`seaLevelPressure`), Météo-France (`pmer`), Met Office (`mslp`), WeatherLink (`bar_sea_level`). Derivado (`p·e^(z/8000)` con altitud de catálogo) en Meteocat, MeteoGalicia, Frost, Meteohub, POEM y Windy. |
| `Td` | derivado | Siempre calculado por MeteoLabX desde `Tc` y `RH` con Magnus-Tetens; nunca se conserva el punto de rocío publicado por el proveedor. |
| `wind`, `gust` | nativo | Conversión m/s→km/h (AEMET, Meteocat, Frost, Meteohub, Windy, NWS, Météo-France, Euskalmet), mph→km/h (WeatherLink), unidad declarada (MeteoGalicia). |
| `wind_dir_deg` | nativo | Cardinal→grados cuando el feed lo da en texto (AEMET, Met Office). |
| `feels_like`, `heat_index`, `wind_chill` | derivado / nativo | Calculados por el pipeline (Steadman/Rothfusz/NOAA); nativos preservados en NWS y WeatherLink. |
| `precip_total` | nativo / derivado | Acumulado diario nativo en WU y WeatherLink. Suma de incrementos de la serie del día en AEMET†, Meteocat, MeteoGalicia, NWS, Météo-France, Euskalmet, Meteohub, POEM. Windy integra `precip_1h` por intervalo como tasa horaria. En Frost, heurística contador-con-resets (**estimado** cuando el contador se reinicia a mitad de día). Met Office no lo expone (null). |
| `solar_radiation`, `uv` | nativo | Solo donde el proveedor lo publica (WU, Meteocat, MeteoGalicia‡, Euskalmet, WeatherLink y Windy para UV). ‡UV de MeteoGalicia: **estimado** desde W/m² (×40) cuando el feed no da índice. |
| `elevation`, `lat`, `lon` | nativo | Del catálogo local del proveedor; fallback a la observación. |

† AEMET no reporta acumulado diario en la observación convencional.

## Derivadas (`ObservationDerivatives`, `/current/processed`)

Todas **derivadas** por `domain.observation_pipeline` (mismas fórmulas
que usaba el frontend): termodinámica (e_sat/e Magnus, Tw, q, θ, Tv,
Te, ρ, LCL), tendencia de presión 3h desde la serie, intensidades de
lluvia 5/10 min, ET0 (FAO-56 horario acumulado), `clarity` (**estimado**:
ratio frente a radiación de cielo despejado teórica) y balance hídrico.

## Extremos diarios (`DailyExtremes`)

**Derivados** en el backend: max/min de la serie del día + la
observación actual. No se usan (de momento) los extremos nativos
intra-horarios que publican algunos proveedores (tx/tn de Météo-France,
códigos 40/42 de Meteocat), así que en días con picos entre lecturas
pueden quedarse cortos por la amplitud intra-intervalo.

## Series (`TodaySeries`, `RecentSeries`)

Mismas reglas que la observación por punto. `pressures` es **siempre
MSL** en el contrato canónico (derivada donde el proveedor solo da
absoluta); `dewpts` se recalcula punto a punto desde temperatura y humedad.
