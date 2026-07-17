#!/usr/bin/env python3
"""
Inyecta los tags PWA (favicon, apple-touch-icon, manifest, theme-color, etc.)
directamente en el ``index.html`` que sirve Streamlit.

Por qué hace falta
------------------
Streamlit sirve siempre su propio ``index.html`` fijo desde el paquete
instalado. Ese HTML solo trae el favicon de Streamlit y ``<title>Streamlit</title>``;
no hay manifest ni apple-touch-icon. Inyectar esos tags por JavaScript en
runtime (desde un iframe de ``components.html``) NO basta porque:

  * Safari lee el favicon una sola vez al parsear el HTML inicial e ignora los
    ``<link rel="icon">`` añadidos luego por JS.
  * iOS/Android, al "Añadir a pantalla de inicio", leen ``apple-touch-icon`` y
    ``manifest`` del HTML tal como llegó del servidor, no del DOM modificado.

La única forma fiable en Streamlit puro es escribir los tags en el HTML real
antes de arrancar el servidor. Este script se ejecuta en el arranque
(ver ``Procfile`` / ``railway.toml``) y es idempotente: re-aplica el bloque en
cada deploy sin duplicarlo.

Mantener en sincronía
---------------------
``ASSET_VERSION`` debe coincidir con ``PWA_ASSET_VERSION`` en ``meteolabx.py``.
Súbela cuando cambien los iconos o el manifest para forzar recarga de caché.
"""
from __future__ import annotations

import re
import shutil
import sys
import json
from pathlib import Path

# Debe coincidir con PWA_ASSET_VERSION en meteolabx.py
ASSET_VERSION = "12"

# En el HTML inicial apuntamos al propio directorio static de Streamlit.
# El script copia ahí los assets PWA antes de arrancar el servidor, así el
# favicon no depende de que /app/static esté activo en producción.
STATIC_BASE = "."
PWA_ASSET_FILENAMES = (
    "favicon.png",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon.ico",
    "apple-touch-icon-pwa.png",
    "icon-192-pwa.png",
    "icon-512-pwa.png",
    "manifest.json",
    "robots.txt",
    "sitemap.xml",
    "og-image.png",  # imagen para tarjetas sociales (Open Graph / Twitter)
)

# SEO / redes sociales. La descripción es lo que se lee bajo el título en
# Google y en la tarjeta al compartir el enlace; edítala aquí.
SITE_URL = "https://www.meteolabx.com"
SITE_TITLE = "MeteoLabX — Panel meteorológico avanzado"
SITE_DESCRIPTION = (
    "Observa y analiza en tiempo real datos de estaciones de múltiples redes "
    "(Weather Underground, AEMET, Meteocat, Met Office y más): gráficos, "
    "tendencias y diagramas termodinámicos."
)
SITE_NOSCRIPT = (
    "MeteoLabX es un panel meteorológico avanzado para observar y analizar "
    "en tiempo real datos de estaciones de AEMET, Meteocat, Met Office, "
    "Weather Underground y más."
)

START_MARKER = "<!-- MLX-PWA-START -->"
END_MARKER = "<!-- MLX-PWA-END -->"
SPLASH_START_MARKER = "<!-- MLX-SPLASH-START -->"
SPLASH_END_MARKER = "<!-- MLX-SPLASH-END -->"


def _build_block() -> str:
    v = ASSET_VERSION
    b = STATIC_BASE
    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebApplication",
            "name": "MeteoLabX",
            "url": SITE_URL,
            "description": SITE_DESCRIPTION,
            "applicationCategory": "WeatherApplication",
            "operatingSystem": "Web",
            "inLanguage": "es-ES",
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "EUR",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""{START_MARKER}
    <title>{SITE_TITLE}</title>
    <link rel="icon" type="image/png" sizes="32x32" href="{b}/favicon-32x32.png?v={v}" />
    <link rel="icon" type="image/png" sizes="16x16" href="{b}/favicon-16x16.png?v={v}" />
    <link rel="shortcut icon" type="image/png" href="{b}/favicon.png?v={v}" />
    <link rel="apple-touch-icon" href="{b}/apple-touch-icon-pwa.png?v={v}" />
    <link rel="apple-touch-icon" sizes="180x180" href="{b}/apple-touch-icon-pwa.png?v={v}" />
    <link rel="apple-touch-icon-precomposed" href="{b}/apple-touch-icon-pwa.png?v={v}" />
    <link rel="manifest" href="{b}/manifest.json?v={v}" />
    <meta name="theme-color" content="#2384ff" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="MeteoLabX" />
    <meta name="application-name" content="MeteoLabX" />
    <meta name="description" content="{SITE_DESCRIPTION}" />
    <meta name="robots" content="index, follow, max-image-preview:large" />
    <link rel="canonical" href="{SITE_URL}/" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="MeteoLabX" />
    <meta property="og:locale" content="es_ES" />
    <meta property="og:url" content="{SITE_URL}/" />
    <meta property="og:title" content="{SITE_TITLE}" />
    <meta property="og:description" content="{SITE_DESCRIPTION}" />
    <meta property="og:image" content="{SITE_URL}/og-image.png?v={v}" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content="MeteoLabX" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{SITE_TITLE}" />
    <meta name="twitter:description" content="{SITE_DESCRIPTION}" />
    <meta name="twitter:image" content="{SITE_URL}/og-image.png?v={v}" />
    <script type="application/ld+json">{json_ld}</script>
    {END_MARKER}"""


def _build_splash() -> str:
    """Splash fijo que cubre únicamente la hidratación inicial de Streamlit."""
    return f"""{SPLASH_START_MARKER}
    <style>
      #mlx-boot-splash {{
        position: fixed; inset: 0; z-index: 2147483000;
        display: flex; align-items: center; justify-content: center;
        background: #0e1117; color: rgba(255,255,255,.94);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        transition: opacity .18s ease;
      }}
      #mlx-boot-splash.mlx-leaving {{ opacity: 0; pointer-events: none; }}
      #mlx-boot-splash .mlx-splash-card {{ text-align: center; padding: 24px; }}
      #mlx-boot-splash img {{ width: 64px; height: 64px; border-radius: 16px; }}
      #mlx-boot-splash strong {{ display: block; margin-top: 12px; font-size: 1.18rem; }}
      #mlx-boot-splash span {{ display: block; margin-top: 5px; font-size: .82rem; opacity: .62; }}
      #mlx-boot-splash .mlx-splash-spinner {{
        width: 24px; height: 24px; margin: 16px auto 0; border-radius: 50%;
        border: 2px solid rgba(255,255,255,.18); border-top-color: #5da8ff;
        animation: mlx-splash-spin .75s linear infinite;
      }}
      @keyframes mlx-splash-spin {{ to {{ transform: rotate(360deg); }} }}
      @media (prefers-color-scheme: light) {{
        #mlx-boot-splash {{ background: #f7f9fc; color: rgba(15,18,25,.94); }}
        #mlx-boot-splash .mlx-splash-spinner {{ border-color: rgba(15,18,25,.14); border-top-color: #2384ff; }}
      }}
    </style>
    <div id="mlx-boot-splash" role="status" aria-live="polite">
      <div class="mlx-splash-card">
        <img src="./icon-192-pwa.png?v={ASSET_VERSION}" alt="" />
        <strong>MeteoLabX</strong>
        <span>Cargando datos meteorológicos…</span>
        <div class="mlx-splash-spinner" aria-hidden="true"></div>
      </div>
    </div>
    {SPLASH_END_MARKER}"""


def _find_index_html() -> Path:
    import streamlit

    index = Path(streamlit.__file__).parent / "static" / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"No se encontró index.html de Streamlit en {index}")
    return index


def _copy_pwa_assets(streamlit_static_dir: Path) -> int:
    repo_static = Path(__file__).resolve().parents[1] / "static"
    copied = 0
    for filename in PWA_ASSET_FILENAMES:
        source = repo_static / filename
        if not source.is_file():
            continue
        shutil.copy2(source, streamlit_static_dir / filename)
        copied += 1
    return copied


def patch(index_path: Path) -> bool:
    """Inserta/actualiza el bloque PWA. Devuelve True si escribió cambios."""
    html = index_path.read_text(encoding="utf-8")
    original = html

    block = _build_block()
    html = re.sub(r'<html\s+lang="[^"]*"', '<html lang="es"', html, count=1)
    html = re.sub(
        r"<noscript>.*?</noscript>",
        f"<noscript>{SITE_NOSCRIPT}</noscript>",
        html,
        count=1,
        flags=re.DOTALL,
    )

    # 1) Reemplazar bloque existente (idempotencia) o insertar antes de </head>.
    existing = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    if existing.search(html):
        html = existing.sub(block, html)
    else:
        # Quitar el favicon por defecto de Streamlit para que no compita.
        html = re.sub(
            r'\s*<link\s+rel="shortcut icon"\s+href="\./favicon\.png"\s*/>',
            "",
            html,
        )
        # Quitar el <title>Streamlit</title> por defecto (lo ponemos en el bloque).
        html = re.sub(r"\s*<title>\s*Streamlit\s*</title>", "", html)

        if "</head>" not in html:
            raise ValueError("No se encontró </head> en index.html")
        html = html.replace("</head>", f"    {block}\n  </head>", 1)

    splash = _build_splash()
    existing_splash = re.compile(
        re.escape(SPLASH_START_MARKER) + r".*?" + re.escape(SPLASH_END_MARKER),
        flags=re.DOTALL,
    )
    if existing_splash.search(html):
        html = existing_splash.sub(splash, html)
    else:
        body_match = re.search(r"<body(?:\s[^>]*)?>", html, flags=re.IGNORECASE)
        if not body_match:
            raise ValueError("No se encontró <body> en index.html")
        insert_at = body_match.end()
        html = html[:insert_at] + "\n    " + splash + html[insert_at:]

    if html == original:
        return False

    index_path.write_text(html, encoding="utf-8")
    return True


def main() -> int:
    try:
        index_path = _find_index_html()
    except Exception as exc:  # noqa: BLE001
        print(f"[patch_streamlit_index] AVISO: {exc}", file=sys.stderr)
        # No abortamos el arranque por esto; la app sigue funcionando sin PWA.
        return 0

    try:
        copied = _copy_pwa_assets(index_path.parent)
        changed = patch(index_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[patch_streamlit_index] AVISO al parchear: {exc}", file=sys.stderr)
        return 0

    state = "parcheado" if changed else "ya estaba al día"
    print(f"[patch_streamlit_index] index.html {state}: {index_path} ({copied} assets PWA copiados)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
