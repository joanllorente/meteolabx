"""
Inyectores de markup/JS para la app Streamlit (extraídos de ``meteolabx.py``).

Generan iframes con ``components.html``: metadata PWA + iconos, el compactor
de Plotly en móvil y el actualizador de "edad del dato" en vivo. Son
autocontenidos (solo dependen de ``streamlit.components.v1``); se importan
desde ``meteolabx.py`` con sus nombres históricos.
"""

from __future__ import annotations

import streamlit.components.v1 as components

# Bump al cambiar iconos / manifest para forzar recarga de assets cacheados.
PWA_ASSET_VERSION = "12"
PWA_STATIC_BASE = "."


def inject_pwa_metadata() -> None:
    """
    Inyecta en un único iframe el setup JS necesario al arrancar la página:

    1. Manifest + iconos en el ``<head>`` real (PWA / iOS home screen).
    2. ``meteolabx_boot_id`` en sessionStorage (lo usaba antes
       ``sync_browser_context_early``).
    3. Limpieza de query params legacy (``_tz``, ``_vw``, ``_cs``,
       ``_mlx_boot``) que versiones anteriores usaban para pasar contexto del
       navegador a Python por URL.

    Antes esto eran dos iframes separados (``_inject_pwa_metadata`` y
    ``sync_browser_context_early``); cada iframe es un sub-documento que el
    navegador inicializa y al que Streamlit envía mensajes, así que reducir a
    uno aligera la carga inicial sin cambiar el comportamiento.
    """
    components.html(
        f"""
        <script>
        (function () {{
          // -------- Canonicalizar dominio: apex -> www --------
          // ``meteolabx.com`` y ``www.meteolabx.com`` son orígenes distintos:
          // no comparten localStorage. El dominio canónico es www; si alguien
          // entra por el apex (p. ej. desde resultados de búsqueda), lo
          // redirigimos a www para que vea sus favoritos/credenciales. Parche
          // hasta que el redirect 301 a nivel de hosting/DNS esté activo.
          try {{
            const _host = (window.parent || window).location;
            if (_host.hostname === "meteolabx.com") {{
              _host.replace("https://www.meteolabx.com" + _host.pathname + _host.search + _host.hash);
              return;
            }}
          }} catch (_e) {{}}

          // -------- Boot id en sessionStorage + limpieza de URL --------
          try {{
            const hostWin = window.parent || window;
            try {{
              if (!hostWin.sessionStorage.getItem("meteolabx_boot_id")) {{
                const bootId = `${{Date.now().toString(36)}}-${{Math.random().toString(36).slice(2, 10)}}`;
                hostWin.sessionStorage.setItem("meteolabx_boot_id", bootId);
              }}
            }} catch (_e) {{}}

            const urlForCleanup = new URL(hostWin.location.href);
            let cleanupChanged = false;
            ["_tz", "_vw", "_cs", "_mlx_boot"].forEach(function (key) {{
              if (urlForCleanup.searchParams.has(key)) {{
                urlForCleanup.searchParams.delete(key);
                cleanupChanged = true;
              }}
            }});
            if (cleanupChanged && hostWin.history && typeof hostWin.history.replaceState === 'function') {{
              hostWin.history.replaceState(null, "", urlForCleanup.toString());
            }}
          }} catch (_e) {{}}

          // -------- PWA / manifest / iconos en el <head> real --------
          try {{
            const doc = window.parent && window.parent.document ? window.parent.document : document;
            const head = doc.head;
            if (!head) return;
            const base = "{PWA_STATIC_BASE}";
            const version = "{PWA_ASSET_VERSION}";
            const asset = (name) => `${{base}}/${{name}}?v=${{version}}`;

            function upsertMeta(name, content) {{
              let el = head.querySelector(`meta[name="${{name}}"]`);
              if (!el) {{
                el = doc.createElement("meta");
                el.setAttribute("name", name);
                head.appendChild(el);
              }}
              el.setAttribute("content", content);
            }}

            function upsertLink(rel, href, attrs) {{
              attrs = attrs || {{}};
              const sizes = attrs.sizes || "";
              const selector = sizes
                ? `link[rel="${{rel}}"][sizes="${{sizes}}"]`
                : `link[rel="${{rel}}"]:not([sizes])`;
              let el = head.querySelector(selector);
              if (!el) {{
                el = doc.createElement("link");
                el.setAttribute("rel", rel);
                if (sizes) el.setAttribute("sizes", sizes);
                head.appendChild(el);
              }}
              Object.keys(attrs).forEach((key) => el.setAttribute(key, attrs[key]));
              el.setAttribute("href", href);
            }}

            upsertMeta("viewport", "width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes");
            upsertMeta("mobile-web-app-capable", "yes");
            upsertMeta("apple-mobile-web-app-capable", "yes");
            upsertMeta("apple-mobile-web-app-status-bar-style", "black-translucent");
            upsertMeta("apple-mobile-web-app-title", "MeteoLabX");
            upsertMeta("theme-color", "#2384ff");

            // Borrar TODOS los <link rel="icon"> y rel="shortcut icon" /
            // rel="apple-touch-icon" que ya pinte Streamlit por defecto.
            // Si no los quitamos, el navegador puede seguir mostrando el
            // logo de Streamlit aunque después añadamos los nuestros.
            const REMOVE_RELS = [
              "icon",
              "shortcut icon",
              "apple-touch-icon",
              "apple-touch-icon-precomposed",
              "mask-icon",
            ];
            REMOVE_RELS.forEach(function (rel) {{
              const existing = head.querySelectorAll(`link[rel="${{rel}}"]`);
              existing.forEach(function (node) {{ node.parentNode.removeChild(node); }});
            }});

            upsertLink("manifest", asset("manifest.json"));

            // --- Favicon de la pestaña del navegador ---
            // Solo PNG. Streamlit sirve los PNG en /app/static/ con
            // Content-Type: image/png (verificado vía curl). Streamlit NO
            // mapea correctamente el MIME type de los .ico (los sirve como
            // text/plain), así que los excluimos: con PNG todos los
            // navegadores modernos pintan el favicon sin problema.
            upsertLink("icon", asset("favicon-32x32.png"), {{ type: "image/png", sizes: "32x32" }});
            upsertLink("icon", asset("favicon-16x16.png"), {{ type: "image/png", sizes: "16x16" }});
            upsertLink("shortcut icon", asset("favicon.png"), {{ type: "image/png" }});

            // --- Icono de PWA / "Añadir a pantalla de inicio" ---
            // Aquí sí usamos el icono azul con isobaras.
            upsertLink("apple-touch-icon", asset("apple-touch-icon-pwa.png"));
            upsertLink("apple-touch-icon", asset("apple-touch-icon-pwa.png"), {{ sizes: "180x180" }});
            upsertLink("apple-touch-icon-precomposed", asset("apple-touch-icon-pwa.png"));
          }} catch (_e) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def remove_boot_splash() -> None:
    """Retira el splash estático solo tras completar la hidratación fiable."""
    components.html(
        """
        <script>
        (function () {
          try {
            const doc = window.parent && window.parent.document ? window.parent.document : document;
            const splash = doc.getElementById("mlx-boot-splash");
            if (!splash) return;
            splash.classList.add("mlx-leaving");
            window.setTimeout(function () { splash.remove(); }, 190);
          } catch (_e) {}
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def inject_mobile_plotly_compactor() -> None:
    """Compacta gráficos Plotly solo en viewports pequeños desde el DOM padre."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          if (!doc) return;

          function isSmallViewport() {
            const vw = Math.round(host.innerWidth || doc.documentElement.clientWidth || 0);
            return vw > 0 && vw <= 900;
          }

          function isNarrowPlot(plot) {
            if (!plot || typeof plot.getBoundingClientRect !== "function") return false;
            const rect = plot.getBoundingClientRect();
            const width = Math.round(rect && rect.width ? rect.width : 0);
            return width > 0 && width <= 460;
          }

          function titleText(axis) {
            if (!axis || axis.title == null) return "";
            if (typeof axis.title === "string") return axis.title;
            return axis.title.text || "";
          }

          function getPlotlyApi(plot) {
            const candidates = [
              host.Plotly,
              window.Plotly,
              plot && plot.ownerDocument && plot.ownerDocument.defaultView && plot.ownerDocument.defaultView.Plotly
            ];
            for (const candidate of candidates) {
              if (candidate && typeof candidate.relayout === "function") return candidate;
            }
            return null;
          }

          function captureOriginal(plot) {
            if (plot.__mlbxOriginalLayout) return plot.__mlbxOriginalLayout;
            const layout = plot.layout || {};
            plot.__mlbxOriginalLayout = {
              margin: {
                l: layout.margin && layout.margin.l != null ? layout.margin.l : 60,
                r: layout.margin && layout.margin.r != null ? layout.margin.r : 40,
                t: layout.margin && layout.margin.t != null ? layout.margin.t : 60,
                b: layout.margin && layout.margin.b != null ? layout.margin.b : 60
              },
              height: layout.height != null ? layout.height : null,
              titleFontSize: layout.title && layout.title.font && layout.title.font.size != null ? layout.title.font.size : null,
              legendFontSize: layout.legend && layout.legend.font && layout.legend.font.size != null ? layout.legend.font.size : null,
              xaxis: {
                title: titleText(layout.xaxis),
                dtick: layout.xaxis && layout.xaxis.dtick != null ? layout.xaxis.dtick : null,
                tickformat: layout.xaxis && layout.xaxis.tickformat != null ? layout.xaxis.tickformat : null,
                tickangle: layout.xaxis && layout.xaxis.tickangle != null ? layout.xaxis.tickangle : 0,
                automargin: !!(layout.xaxis && layout.xaxis.automargin),
                nticks: layout.xaxis && layout.xaxis.nticks != null ? layout.xaxis.nticks : null,
                tickfontSize: layout.xaxis && layout.xaxis.tickfont && layout.xaxis.tickfont.size != null ? layout.xaxis.tickfont.size : null,
                fixedrange: !!(layout.xaxis && layout.xaxis.fixedrange)
              },
              yaxis: {
                title: titleText(layout.yaxis),
                automargin: !!(layout.yaxis && layout.yaxis.automargin),
                tickfontSize: layout.yaxis && layout.yaxis.tickfont && layout.yaxis.tickfont.size != null ? layout.yaxis.tickfont.size : null,
                ticklabelposition: layout.yaxis && layout.yaxis.ticklabelposition != null ? layout.yaxis.ticklabelposition : null,
                ticklabelstandoff: layout.yaxis && layout.yaxis.ticklabelstandoff != null ? layout.yaxis.ticklabelstandoff : null,
                fixedrange: !!(layout.yaxis && layout.yaxis.fixedrange)
              },
              yaxis2: {
                title: titleText(layout.yaxis2),
                automargin: !!(layout.yaxis2 && layout.yaxis2.automargin),
                tickfontSize: layout.yaxis2 && layout.yaxis2.tickfont && layout.yaxis2.tickfont.size != null ? layout.yaxis2.tickfont.size : null,
                ticklabelposition: layout.yaxis2 && layout.yaxis2.ticklabelposition != null ? layout.yaxis2.ticklabelposition : null,
                ticklabelstandoff: layout.yaxis2 && layout.yaxis2.ticklabelstandoff != null ? layout.yaxis2.ticklabelstandoff : null,
                fixedrange: !!(layout.yaxis2 && layout.yaxis2.fixedrange)
              }
            };
            return plot.__mlbxOriginalLayout;
          }

          function compactPlot(plot) {
            if (!plot || !plot.layout || !plot.layout.xaxis) return;
            const plotlyApi = getPlotlyApi(plot);
            if (!plotlyApi) return;
            const original = captureOriginal(plot);
            if (plot.dataset.mlbxCompactMode === "mobile") return;
            const currentHeight = plot.layout && plot.layout.height != null ? plot.layout.height : null;
            const targetHeight = currentHeight == null || currentHeight > 312 ? 272 : currentHeight;
            plotlyApi.relayout(plot, {
              "margin.l": 16,
              "margin.r": 12,
              "margin.t": 52,
              "margin.b": 30,
              "height": targetHeight,
              "title.font.size": 15,
              "legend.font.size": 9,
              "xaxis.title.text": "",
              "xaxis.dtick": 3 * 60 * 60 * 1000,
              "xaxis.tickformat": "%H",
              "xaxis.tickangle": 0,
              "xaxis.automargin": false,
              "xaxis.nticks": 4,
              "xaxis.tickfont.size": 11,
              "xaxis.ticklabeloverflow": "allow",
              "xaxis.fixedrange": true,
              "yaxis.title.text": "",
              "yaxis.automargin": false,
              "yaxis.tickfont.size": 11,
              "yaxis.ticklabelposition": "inside",
              "yaxis.ticklabelstandoff": -2,
              "yaxis.fixedrange": true,
              "yaxis2.title.text": "",
              "yaxis2.automargin": false,
              "yaxis2.tickfont.size": 11,
              "yaxis2.ticklabelposition": "inside",
              "yaxis2.ticklabelstandoff": -2,
              "yaxis2.fixedrange": true,
              "dragmode": false
            }).then(function () {
              plot.dataset.mlbxCompactMode = "mobile";
            }).catch(function () {});
          }

          function restorePlot(plot) {
            const original = plot && plot.__mlbxOriginalLayout;
            if (!plot || !original || plot.dataset.mlbxCompactMode !== "mobile") return;
            const plotlyApi = getPlotlyApi(plot);
            if (!plotlyApi) return;
            plotlyApi.relayout(plot, {
              "margin.l": original.margin.l,
              "margin.r": original.margin.r,
              "margin.t": original.margin.t,
              "margin.b": original.margin.b,
              "height": original.height,
              "title.font.size": original.titleFontSize,
              "legend.font.size": original.legendFontSize,
              "xaxis.title.text": original.xaxis.title,
              "xaxis.dtick": original.xaxis.dtick,
              "xaxis.tickformat": original.xaxis.tickformat,
              "xaxis.tickangle": original.xaxis.tickangle,
              "xaxis.automargin": original.xaxis.automargin,
              "xaxis.nticks": original.xaxis.nticks,
              "xaxis.tickfont.size": original.xaxis.tickfontSize,
              "xaxis.fixedrange": original.xaxis.fixedrange,
              "yaxis.title.text": original.yaxis.title,
              "yaxis.automargin": original.yaxis.automargin,
              "yaxis.tickfont.size": original.yaxis.tickfontSize,
              "yaxis.ticklabelposition": original.yaxis.ticklabelposition,
              "yaxis.ticklabelstandoff": original.yaxis.ticklabelstandoff,
              "yaxis.fixedrange": original.yaxis.fixedrange,
              "yaxis2.title.text": original.yaxis2.title,
              "yaxis2.automargin": original.yaxis2.automargin,
              "yaxis2.tickfont.size": original.yaxis2.tickfontSize,
              "yaxis2.ticklabelposition": original.yaxis2.ticklabelposition,
              "yaxis2.ticklabelstandoff": original.yaxis2.ticklabelstandoff,
              "yaxis2.fixedrange": original.yaxis2.fixedrange
            }).then(function () {
              plot.dataset.mlbxCompactMode = "desktop";
            }).catch(function () {});
          }

          function syncPlots() {
            const plots = Array.from(doc.querySelectorAll('[data-testid="stPlotlyChart"] .js-plotly-plot'));
            plots.forEach(function (plot) {
              if (isSmallViewport() || isNarrowPlot(plot)) compactPlot(plot);
              else restorePlot(plot);
            });
          }

          function schedulePlotSync() {
            if (host.__mlbxViewportPlotRaf) return;
            host.__mlbxViewportPlotRaf = host.requestAnimationFrame(function () {
              host.__mlbxViewportPlotRaf = null;
              syncPlots();
            });
          }

          function bootstrapPlotSync(attempts) {
            schedulePlotSync();
            if (attempts <= 0) return;
            host.setTimeout(function () {
              bootstrapPlotSync(attempts - 1);
            }, 350);
          }

          bootstrapPlotSync(10);

          if (!host.__mlbxViewportPlotResizeBound) {
            host.__mlbxViewportPlotResizeBound = true;
            host.addEventListener("resize", schedulePlotSync, { passive: true });
            host.addEventListener("pageshow", schedulePlotSync, { passive: true });
          }

          if (!host.__mlbxViewportPlotObserverBound && host.MutationObserver && doc.body) {
            host.__mlbxViewportPlotObserverBound = true;
            const observer = new host.MutationObserver(function () {
              schedulePlotSync();
            });
            observer.observe(doc.body, { childList: true, subtree: true });
            host.__mlbxViewportPlotObserver = observer;
          }
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def inject_live_age_updater() -> None:
    """Mantiene actualizados edad y hora local del usuario sin esperar a un rerun completo."""
    components.html(
        """
        <script>
        (function () {
          const host = window.parent || window;
          const doc = host.document;
          if (!doc || !doc.body) return;

          function formatAge(epoch) {
            const now = Math.floor(Date.now() / 1000);
            const diff = Math.max(0, now - epoch);
            if (diff < 60) return `${diff}s`;
            if (diff < 3600) return `${Math.floor(diff / 60)}m`;
            return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`;
          }

          function formatLocalDateTime(epoch) {
            const date = new Date(epoch * 1000);
            if (!Number.isFinite(date.getTime())) return "";
            const pad = function (value) {
              return String(value).padStart(2, "0");
            };
            return [
              pad(date.getDate()),
              pad(date.getMonth() + 1),
              date.getFullYear()
            ].join("-") + " " + [
              pad(date.getHours()),
              pad(date.getMinutes()),
              pad(date.getSeconds())
            ].join(":");
          }

          function refreshUserTimes() {
            doc.querySelectorAll(".mlbx-live-user-time[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              const text = formatLocalDateTime(epoch);
              if (text && el.textContent !== text) el.textContent = text;
            });
            doc.querySelectorAll(".mlbx-live-user-time-label").forEach(function (el) {
              const fallback = el.getAttribute("data-fallback-label") || "Hora usuario";
              if (el.textContent !== fallback) el.textContent = fallback;
            });
          }

          function refreshAges() {
            refreshUserTimes();
            doc.querySelectorAll(".mlbx-live-age[data-epoch]").forEach(function (el) {
              const epoch = Number.parseInt(el.getAttribute("data-epoch") || "", 10);
              if (!Number.isFinite(epoch)) return;
              const text = formatAge(epoch);
              if (el.textContent !== text) el.textContent = text;
            });
          }

          function runNow() {
            try {
              refreshAges();
            } catch (err) {
              console.debug("MeteoLabX age refresh error", err);
            }
          }

          function ensureInterval() {
            if (host.__mlbxAgeInterval) {
              host.clearInterval(host.__mlbxAgeInterval);
            }
            host.__mlbxAgeInterval = host.setInterval(runNow, 1000);
          }

          if (!host.__mlbxAgeRefreshBound) {
            host.__mlbxAgeRefreshBound = true;
            host.addEventListener("pageshow", runNow, { passive: true });
            host.addEventListener("focus", runNow, { passive: true });
            doc.addEventListener("visibilitychange", runNow, { passive: true });
            if (host.MutationObserver && doc.body) {
              host.__mlbxAgeObserver = new host.MutationObserver(runNow);
              host.__mlbxAgeObserver.observe(doc.body, { childList: true, subtree: true });
            }
          }

          runNow();
          ensureInterval();
        })();
        </script>
        """,
        height=0,
        width=0,
    )
