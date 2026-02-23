"""
Componentes de tarjetas y grillas para visualizacion de datos
"""

from functools import lru_cache
from html import escape
from pathlib import Path
import unicodedata

import streamlit as st

from utils.helpers import html_clean
from .icons import icon_img


DEFINITIONS_PATH = Path("/Users/joantisdale/Desktop/definiciones.txt")
FALLBACK_DEFINITIONS = {
    "temperatura": "Temperatura del aire medida por la estación a la altura del sensor (habitualmente 1.5-2 m).",
    "humedad relativa": "Porcentaje de vapor de agua presente en el aire respecto al máximo posible a esa temperatura.",
    "punto de rocio": "Temperatura a la que el aire se saturaría y comenzaría la condensación si se enfría a presión constante.",
    "presion": "Presión atmosférica medida por el barómetro de la estación. Puede mostrarse como presión absoluta o referida al nivel del mar.",
    "viento": "Velocidad media del viento en el intervalo de medida. Suele acompañarse de dirección y racha máxima.",
    "precipitacion hoy": "Precipitación acumulada desde las 00:00 (hora local) hasta el instante actual.",
    "humedad especifica": "Masa de vapor de agua por unidad de masa de aire húmedo (g/kg).",
    "humedad absoluta": "Masa de vapor de agua por unidad de volumen de aire (g/m³).",
    "temperatura de bulbo humedo": "Temperatura que alcanzaría el aire al enfriarse por evaporación hasta saturación, a presión aproximadamente constante.",
    "temperatura virtual": "Temperatura equivalente que tendría aire seco con la misma densidad que el aire húmedo observado.",
    "temperatura equivalente": "Temperatura que resultaría al condensar todo el vapor de agua del aire y liberar su calor latente.",
    "temperatura potencial": "Temperatura que tendría una parcela de aire al llevarla adiabáticamente a 1000 hPa.",
    "densidad del aire": "Masa de aire por unidad de volumen, calculada a partir de temperatura, humedad y presión.",
    "nivel de condensacion por ascenso": "Altura aproximada a la que una parcela de aire ascendente alcanzaría saturación (base de nube LCL).",
    "radiacion solar": "Irradiancia solar global instantánea medida por el sensor (W/m²).",
    "indice uv": "Índice de radiación ultravioleta eritemática en superficie.",
    "evapotranspircion": "Pérdida de agua combinada por evaporación y transpiración estimada para el día actual (mm).",
    "claridad del cielo": "Índice relativo de transparencia atmosférica deducido de la radiación observada frente a la potencial.",
    "balance hidrico": "Diferencia entre precipitación acumulada y evapotranspiración estimada en el día actual (mm).",
}


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower().strip()
    for src, dst in ((".", " "), (":", " "), ("_", " "), ("/", " ")):
        normalized = normalized.replace(src, dst)
    return " ".join(normalized.split())


@lru_cache(maxsize=1)
def _load_definitions() -> dict:
    definitions = {}
    if not DEFINITIONS_PATH.exists():
        return definitions

    current_key = ""
    current_parts = []

    lines = DEFINITIONS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if set(stripped) == {"-"}:
            continue

        is_top_level = raw_line.startswith("- ")
        is_child = raw_line.startswith("\t- ") or raw_line.startswith("    - ")

        if is_top_level:
            if current_key:
                definitions[current_key] = "\n".join(part for part in current_parts if part)

            payload = stripped[2:].strip()
            if ":" in payload:
                label, desc = payload.split(":", 1)
                current_key = _normalize_text(label)
                current_parts = [desc.strip()] if desc.strip() else []
            else:
                current_key = _normalize_text(payload)
                current_parts = []
            continue

        if is_child and current_key:
            child_text = stripped[2:].strip()
            if child_text:
                current_parts.append(f"- {child_text}")
            continue

        if current_key:
            current_parts.append(stripped)

    if current_key:
        definitions[current_key] = "\n".join(part for part in current_parts if part)

    return definitions


def _card_tooltip_text(title: str) -> str:
    definitions = _load_definitions()
    normalized_title = _normalize_text(title)

    aliases = {
        "temp bulbo humedo": "temperatura de bulbo humedo",
        "temp virtual": "temperatura virtual",
        "temp equivalente": "temperatura equivalente",
        "temp potencial": "temperatura potencial",
        "base nube lcl": "nivel de condensacion por ascenso",
        "evapotranspiracion hoy": "evapotranspircion",
        "balance hidrico hoy": "balance hidrico",
    }

    lookup = aliases.get(normalized_title, normalized_title)
    text = definitions.get(lookup)
    if normalized_title == "radiacion solar":
        extra = "- Energía hoy: integración de la irradiancia solar desde las 00:00 hasta ahora, expresada en MJ/m²."
        if text:
            return f"{text}\n{extra}"
        return f"Radiación solar instantánea medida por piranómetro.\n{extra}"
    if not text:
        text = FALLBACK_DEFINITIONS.get(lookup)
    if text:
        return text
    return "Definicion no disponible todavia para esta variable."


def _capitalize_tooltip_line(text: str) -> str:
    line = text.strip()
    if not line:
        return ""
    if line.startswith("- "):
        payload = line[2:].strip()
        if payload:
            return f"- {payload[:1].upper()}{payload[1:]}"
        return line
    return f"{line[:1].upper()}{line[1:]}"


def _tooltip_html(text: str) -> str:
    fixed_text = text
    fixed_text = fixed_text.replace("esatción", "estación")
    fixed_text = fixed_text.replace(
        "Es medida directamente por la estación con mediante un termistor",
        "Es medida por la estación mediante un termistor",
    )
    fixed_text = fixed_text.replace(
        "Es medida directamente por la estación mediante un termistor",
        "Es medida por la estación mediante un termistor",
    )
    lines = [line for line in fixed_text.splitlines() if line.strip()]
    if not lines:
        return "Definicion no disponible todavia para esta variable."
    normalized = [_capitalize_tooltip_line(line) for line in lines]
    return "<br><br>".join(escape(line) for line in normalized)


def card(
    title: str,
    value: str,
    unit: str = "",
    icon_kind: str = "temp",
    subtitle_html: str = "",
    side_html: str = "",
    uid: str = "x",
    dark: bool = False,
) -> str:
    """
    Genera HTML de una tarjeta de dato meteorologico.
    """
    unit_html = f"<span class='unit'>{unit}</span>" if unit else ""
    sub_html = f"<div class='subtitle'>{subtitle_html}</div>" if subtitle_html else ""
    icon_html = icon_img(icon_kind, uid=uid, dark=dark)

    tip_text = _card_tooltip_text(title)
    tip_html = _tooltip_html(tip_text)

    side_col = f"""
    <div class=\"side-col\">{side_html}</div>
    """ if side_html else ""

    return html_clean(
        f"""
  <div class="card card-h">
    <div class="card-help-wrap" tabindex="0" aria-label="Ayuda de {escape(title)}">
      <span class="card-help-btn">?</span>
      <div class="card-help-tooltip">{tip_html}</div>
    </div>

    <div class="icon-col">
      <div class="icon big">{icon_html}</div>
    </div>

    <div class="content-col">
      <div class="card-title">{title}</div>
      <div class="card-value">{value}{unit_html}</div>
      {sub_html}
    </div>
    {side_col}
  </div>
"""
    )


def section_title(text: str):
    """
    Renderiza un titulo de seccion.
    """
    st.markdown(f"<div class='section-title'>{text}</div>", unsafe_allow_html=True)


def render_grid(cards: list, cols: int = 3, extra_class: str = ""):
    """
    Renderiza una grilla de tarjetas.
    """
    cards_html = "".join(cards)
    html = f"<div class='grid grid-{cols} {extra_class}'>{cards_html}</div>"
    st.markdown(html, unsafe_allow_html=True)
