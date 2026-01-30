"""
Generador de iconos SVG para las tarjetas meteorológicas
"""
import base64
from utils.helpers import html_clean


def icon_svg(kind: str, uid: str, dark: bool = False) -> str:
    """
    Genera SVG de icono según el tipo
    
    Args:
        kind: Tipo de icono (temp, dew, rh, press, wind, rain)
        uid: ID único para evitar colisiones en gradientes
        dark: Tema oscuro activado
        
    Returns:
        String con SVG completo
    """
    stroke = "rgba(255,255,255,0.55)" if dark else "rgba(0,0,0,0.12)"
    glow1 = "rgba(255,255,255,0.35)" if dark else "rgba(255,255,255,0.55)"
    glow2 = "rgba(0,0,0,0.22)" if dark else "rgba(0,0,0,0.10)"
    g = lambda name: f"{name}-{uid}"

    if kind == "temp":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#FFD56A"/>
              <stop offset="0.55" stop-color="#FF8A5B"/>
              <stop offset="1" stop-color="#5E8BFF"/>
            </linearGradient>
            <filter id="{g('glow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="3.2" result="b"/>
              <feColorMatrix in="b" type="matrix"
                values="1 0 0 0 0
                        0 1 0 0 0
                        0 0 1 0 0
                        0 0 0 0.45 0" result="g"/>
              <feMerge>
                <feMergeNode in="g"/>
                <feMergeNode in="SourceGraphic"/>
              </feMerge>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <path d="M17 35c0 5.5 4.5 10 10 10s10-4.5 10-10c0-3.1-1.5-5.9-3.8-7.7V16.5C33.2 11.8 30.4 8 27 8s-6.2 3.8-6.2 8.5v10.8C18.5 29.1 17 31.9 17 35z"
                fill="white" opacity="0.28" filter="url(#{g('glow')})"/>
          <path d="M27 12c1.5 0 2.7 2 2.7 4.5V32a5.5 5.5 0 1 1-5.4 0V16.5C24.3 14 25.5 12 27 12z"
                fill="white" opacity="0.85"/>
          <circle cx="29" cy="14.6" r="1.0" fill="{glow1}" opacity="0.9"/>
        </svg>
        """)

    if kind == "dew":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#B9E6FF"/>
              <stop offset="1" stop-color="#5AA8FF"/>
            </linearGradient>
            <radialGradient id="{g('drop')}" cx="35%" cy="25%" r="70%">
              <stop offset="0" stop-color="#E9F7FF"/>
              <stop offset="0.5" stop-color="#7CC7FF"/>
              <stop offset="1" stop-color="#2F7BFF"/>
            </radialGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="5" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <path filter="url(#{g('shadow')})"
            d="M27 10c0 0 11 14 11 21.5C38 38.4 33.1 44 27 44s-11-5.6-11-12.5C16 24 27 10 27 10z"
            fill="url(#{g('drop')})"/>
          <path d="M22 27c2-3 6-5 10-5" stroke="rgba(255,255,255,0.6)" stroke-width="3" stroke-linecap="round"/>
          <path d="M20.5 34.5c2 2.5 6 3.5 9.5 2.5" stroke="rgba(255,255,255,0.45)" stroke-width="3" stroke-linecap="round"/>
        </svg>
        """)

    if kind == "rh":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#73E0FF"/>
              <stop offset="1" stop-color="#2F80ED"/>
            </linearGradient>
            <linearGradient id="{g('ring')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="rgba(255,255,255,0.95)"/>
              <stop offset="1" stop-color="rgba(255,255,255,0.55)"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <circle cx="27" cy="27" r="15.5" fill="rgba(255,255,255,0.18)"/>
            <circle cx="27" cy="27" r="15.5" fill="none" stroke="url(#{g('ring')})" stroke-width="3.5" opacity="0.75"/>
            <path d="M27 15.5 A11.5 11.5 0 0 1 38.5 27"
                  fill="none" stroke="rgba(255,255,255,0.95)" stroke-width="4" stroke-linecap="round"/>
            <circle cx="38.5" cy="27" r="2.2" fill="white" opacity="0.9"/>
          </g>
        </svg>
        """)

    if kind == "press":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#C8A8FF"/>
              <stop offset="1" stop-color="#FFB6D5"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <circle cx="27" cy="28" r="14.5" fill="rgba(255,255,255,0.18)"/>
            <circle cx="27" cy="28" r="14.5" fill="none" stroke="rgba(255,255,255,0.75)" stroke-width="3"/>
            <path d="M27 28 L36 19" stroke="rgba(255,255,255,0.95)" stroke-width="3.2" stroke-linecap="round"/>
            <circle cx="27" cy="28" r="3" fill="white" opacity="0.9"/>
            <path d="M16 28a11 11 0 0 0 22 0" stroke="{stroke}" stroke-width="2.2" stroke-linecap="round"/>
          </g>
        </svg>
        """)

    if kind == "wind":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#7DFFB5"/>
              <stop offset="1" stop-color="#48C6EF"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round">
            <path d="M14 26c8 0 10-6 18-6 5 0 8 2.5 8 6" stroke-width="3.2"/>
            <path d="M14 32c10 0 12-4 20-4 4 0 7 2 7 5" stroke-width="3.0" opacity="0.9"/>
            <path d="M14 20c7 0 9-3 14-3 3 0 5 1.5 5 4" stroke-width="2.6" opacity="0.75"/>
          </g>
        </svg>
        """)

    if kind == "rain":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#FF8A65"/>
              <stop offset="1" stop-color="#FFD180"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>
          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})">
            <path d="M18 30c-2.8 0-5-2.2-5-5 0-2.4 1.6-4.4 3.8-4.9
                     1-3.4 4.1-5.9 7.8-5.9 3.6 0 6.7 2.3 7.7 5.6
                     2.9 0.2 5.2 2.6 5.2 5.6 0 3.1-2.5 5.6-5.6 5.6H18z"
                  fill="rgba(255,255,255,0.92)"/>
            <path d="M22 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
            <path d="M29 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
            <path d="M36 35l-2.2 4.2" stroke="rgba(255,255,255,0.85)" stroke-width="3" stroke-linecap="round"/>
          </g>
        </svg>
        """)

    # --- RADIACIÓN: nuevos iconos ---
    if kind == "solar":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#FFE07A"/>
              <stop offset="0.6" stop-color="#FFB24A"/>
              <stop offset="1" stop-color="#FF6A55"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>

          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="27" cy="25.5" r="6.2" stroke-width="3.2"/>
            <!-- rays -->
            <path d="M27 11.5v4.2" stroke-width="3.0" opacity="0.95"/>
            <path d="M27 35.3v4.2" stroke-width="3.0" opacity="0.95"/>
            <path d="M13.0 25.5h4.2" stroke-width="3.0" opacity="0.95"/>
            <path d="M36.8 25.5h4.2" stroke-width="3.0" opacity="0.95"/>
            <path d="M17.1 15.6l3.0 3.0" stroke-width="2.8" opacity="0.85"/>
            <path d="M33.9 32.4l3.0 3.0" stroke-width="2.8" opacity="0.85"/>
            <path d="M36.9 15.6l-3.0 3.0" stroke-width="2.8" opacity="0.85"/>
            <path d="M20.1 32.4l-3.0 3.0" stroke-width="2.8" opacity="0.85"/>
          </g>
        </svg>
        """)

    if kind == "uv":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#A78BFA"/>
              <stop offset="0.55" stop-color="#7C3AED"/>
              <stop offset="1" stop-color="#EC4899"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>

          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>
          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round" stroke-linejoin="round">
            <!-- small sun -->
            <circle cx="20" cy="19" r="5.2" stroke-width="3.0"/>
            <path d="M20 10.8v3.2M20 24.0v3.2M11.8 19h3.2M25.0 19h3.2" stroke-width="2.7" opacity="0.9"/>
            <path d="M14.4 13.4l2.3 2.3M23.3 22.3l2.3 2.3M25.6 13.4l-2.3 2.3M16.7 22.3l-2.3 2.3" stroke-width="2.4" opacity="0.75"/>
          </g>

          <!-- UV text -->
          <g filter="url(#{g('shadow')})" fill="white" opacity="0.92">
            <text x="27" y="40.3" text-anchor="middle" font-family="system-ui, -apple-system, Segoe UI, Roboto, Arial"
                  font-size="13.5" font-weight="800" letter-spacing="0.5">UV</text>
          </g>
        </svg>
        """)

    if kind == "et0":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#7DFFB5"/>
              <stop offset="1" stop-color="#48C6EF"/>
            </linearGradient>
            <radialGradient id="{g('drop')}" cx="35%" cy="25%" r="75%">
              <stop offset="0" stop-color="#E9FFF7"/>
              <stop offset="0.55" stop-color="#7DE8D2"/>
              <stop offset="1" stop-color="#2FB7FF"/>
            </radialGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>

          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>

          <g filter="url(#{g('shadow')})">
            <!-- droplet -->
            <path d="M27 14c0 0 8 9.5 8 15.0c0 4.7-3.6 8.5-8 8.5s-8-3.8-8-8.5C19 23.5 27 14 27 14z"
                  fill="url(#{g('drop')})" opacity="0.95"/>
            <!-- vapor waves -->
            <path d="M18 41c2.2-1.6 2.2-3.4 0-5.0c-2.2-1.6-2.2-3.4 0-5.0"
                  fill="none" stroke="rgba(255,255,255,0.92)" stroke-width="2.7" stroke-linecap="round" opacity="0.9"/>
            <path d="M36 41c2.2-1.6 2.2-3.4 0-5.0c-2.2-1.6-2.2-3.4 0-5.0"
                  fill="none" stroke="rgba(255,255,255,0.92)" stroke-width="2.7" stroke-linecap="round" opacity="0.9"/>
          </g>
        </svg>
        """)

    if kind == "clarity":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#B9E6FF"/>
              <stop offset="1" stop-color="#5AA8FF"/>
            </linearGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>

          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>

          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round" stroke-linejoin="round">
            <!-- small sun -->
            <circle cx="18.5" cy="20.0" r="5.5" stroke-width="3.0"/>
            <path d="M18.5 11.4v3.0M18.5 25.6v3.0M9.9 20h3.0M24.1 20h3.0" stroke-width="2.6" opacity="0.85"/>
            <!-- clarity bars -->
            <path d="M29.5 34.8h14.0" stroke-width="3.2" opacity="0.95"/>
            <path d="M29.5 29.0h10.5" stroke-width="3.0" opacity="0.85"/>
            <path d="M29.5 23.2h7.2" stroke-width="2.8" opacity="0.75"/>
          </g>
        </svg>
        """)

    if kind == "balance":
        return html_clean(f"""
        <svg width="54" height="54" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="{g('bg')}" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#73E0FF"/>
              <stop offset="1" stop-color="#48C6EF"/>
            </linearGradient>
            <radialGradient id="{g('drop')}" cx="35%" cy="25%" r="75%">
              <stop offset="0" stop-color="#E9F7FF"/>
              <stop offset="0.55" stop-color="#7CC7FF"/>
              <stop offset="1" stop-color="#2F7BFF"/>
            </radialGradient>
            <filter id="{g('shadow')}" x="-40%" y="-40%" width="180%" height="180%">
              <feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="{glow2}" flood-opacity="0.35"/>
            </filter>
          </defs>

          <rect x="1.5" y="1.5" rx="18" ry="18" width="51" height="51" fill="url(#{g('bg')})" opacity="0.95"/>

          <g filter="url(#{g('shadow')})" fill="none" stroke="rgba(255,255,255,0.92)" stroke-linecap="round" stroke-linejoin="round">
            <!-- droplet -->
            <path d="M27 14c0 0 8 9.5 8 15.0c0 4.7-3.6 8.5-8 8.5s-8-3.8-8-8.5C19 23.5 27 14 27 14z"
                  fill="url(#{g('drop')})" opacity="0.95" stroke="none"/>
            <!-- balance arrows -->
            <path d="M16 41h22" stroke-width="3.0" opacity="0.9"/>
            <path d="M20.5 41l-2.4-2.4M20.5 41l-2.4 2.4" stroke-width="2.6" opacity="0.9"/>
            <path d="M33.5 41l2.4-2.4M33.5 41l2.4 2.4" stroke-width="2.6" opacity="0.9"/>
          </g>
        </svg>
        """)

    return ""


def icon_img(kind: str, uid: str, dark: bool = False) -> str:
    """
    Convierte SVG a imagen base64 embebida
    
    Args:
        kind: Tipo de icono
        uid: ID único
        dark: Tema oscuro
        
    Returns:
        HTML img tag con SVG embebido
    """
    svg = icon_svg(kind, uid=uid, dark=dark)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"<img class='icon-img' src='data:image/svg+xml;base64,{b64}'/>"
