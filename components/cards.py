"""
Componentes de tarjetas y grillas para visualizacion de datos
"""

from functools import lru_cache
from html import escape
from pathlib import Path
import unicodedata

import streamlit as st

from utils.helpers import html_clean
from utils.i18n import get_language, t
from .icons import icon_img


DEFINITIONS_PATH = Path(__file__).parent.parent / "definiciones.txt"
FALLBACK_DEFINITIONS_ES = {
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
    "dosis eritematica": "Dosis eritemática acumulada de radiación UV desde las 00:00 hasta el momento actual.\n- 1 SED equivale a 100 J/m² de radiación eritemática efectiva.\n- Fototipo I: eritema visible aproximadamente desde 2 a 3 SED.\n- Fototipo II: aproximadamente 2.5 a 3.5 SED.\n- Fototipo III: aproximadamente 3.5 a 5 SED.\n- Fototipo IV: aproximadamente 5 a 7 SED.\n- Fototipo V: aproximadamente 7 a 10 SED.\n- Fototipo VI: habitualmente por encima de 10 SED.",
    "evapotranspiracion": "Pérdida de agua combinada por evaporación y transpiración estimada para el día actual (mm).",
    "claridad del cielo": "Índice relativo de transparencia atmosférica deducido de la radiación observada frente a la potencial.",
    "altura del sol": "Altura angular del Sol sobre el horizonte para la ubicación y el instante de observación.",
    "balance hidrico": "Diferencia entre precipitación acumulada y evapotranspiración estimada en el día actual (mm).",
}

FALLBACK_DEFINITIONS_EN = {
    "temperatura": "Air temperature measured by the station at sensor height (usually 1.5-2 m).",
    "humedad relativa": "Percentage of water vapour present in the air relative to the maximum possible at that temperature.",
    "punto de rocio": "Temperature at which the air would become saturated and condensation would begin if cooled at constant pressure.",
    "presion": "Atmospheric pressure measured by the station barometer. It may be shown as absolute pressure or reduced to sea level.",
    "viento": "Mean wind speed during the measurement interval. It is usually accompanied by direction and maximum gust.",
    "precipitacion hoy": "Accumulated precipitation from 00:00 local time until the current observation.",
    "humedad especifica": "Mass of water vapour per unit mass of moist air (g/kg).",
    "humedad absoluta": "Mass of water vapour per unit volume of air (g/m³).",
    "temperatura de bulbo humedo": "Temperature the air would reach if cooled by evaporation to saturation at approximately constant pressure.",
    "temperatura virtual": "Equivalent temperature that dry air would have with the same density as the observed moist air.",
    "temperatura equivalente": "Temperature resulting from condensing all the water vapour in the air and releasing its latent heat.",
    "temperatura potencial": "Temperature an air parcel would have if brought adiabatically to 1000 hPa.",
    "densidad del aire": "Mass of air per unit volume, calculated from temperature, humidity and pressure.",
    "nivel de condensacion por ascenso": "Approximate height at which a rising air parcel would reach saturation (LCL cloud base).",
    "radiacion solar": "Instantaneous global solar irradiance measured by the sensor (W/m²).",
    "indice uv": "Surface erythemal ultraviolet radiation index.",
    "dosis eritematica": "Accumulated erythemal UV dose from 00:00 until the current moment.\n- 1 SED equals 100 J/m² of effective erythemal radiation.\n- Skin phototype I: visible erythema roughly from 2 to 3 SED.\n- Skin phototype II: roughly 2.5 to 3.5 SED.\n- Skin phototype III: roughly 3.5 to 5 SED.\n- Skin phototype IV: roughly 5 to 7 SED.\n- Skin phototype V: roughly 7 to 10 SED.\n- Skin phototype VI: usually above 10 SED.",
    "evapotranspiracion": "Combined water loss by evaporation and transpiration estimated for the current day (mm).",
    "claridad del cielo": "Relative atmospheric transparency index derived from observed radiation versus the theoretical potential.",
    "altura del sol": "Solar angular height above the horizon for the observation location and time.",
    "balance hidrico": "Difference between accumulated precipitation and estimated evapotranspiration during the current day (mm).",
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
    current_parts: list[str] = []

    def _flush():
        if current_key:
            definitions[current_key] = "\n".join(p for p in current_parts if p)

    lines = DEFINITIONS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Separador: línea compuesta solo de = o de -
        if stripped and set(stripped) <= {"=", "-"}:
            _flush()
            current_key = ""
            current_parts = []
            continue

        is_child = raw_line.startswith("\t- ") or raw_line.startswith("    - ")
        is_top_level = (not is_child) and stripped.startswith("- ")

        if is_top_level:
            # Nueva entrada principal (puede ser primera del bloque o cambio de clave)
            if current_key:
                _flush()
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

        # Línea sin guión: puede ser comienzo de bloque (con :) o continuación
        if not current_key and ":" in stripped:
            label, desc = stripped.split(":", 1)
            current_key = _normalize_text(label)
            current_parts = [desc.strip()] if desc.strip() else []
            continue

        if current_key:
            current_parts.append(stripped)

    _flush()
    return definitions


def _card_tooltip_text(title: str, tooltip_key: str = "") -> str:
    lang = get_language()
    definitions = _load_definitions() if lang == "es" else {}
    normalized_title = _normalize_text(tooltip_key or title)

    aliases = {
        "temp bulbo humedo": "temperatura de bulbo humedo",
        "temp virtual": "temperatura virtual",
        "temp equivalente": "temperatura equivalente",
        "temp potencial": "temperatura potencial",
        "base nube lcl": "nivel de condensacion por ascenso",
        "irradiancia": "radiacion solar",
        "evapotranspiracion hoy": "evapotranspiracion",
        "balance hidrico hoy": "balance hidrico",
    }

    lookup = aliases.get(normalized_title, normalized_title)
    text = definitions.get(lookup)
    # Si no hay match directo, buscar por prefijo (ej. "humedad especifica" → "humedad especifica (q)")
    if not text:
        for key, val in definitions.items():
            if key.startswith(lookup):
                text = val
                break
    if normalized_title in ("radiacion solar", "irradiancia"):
        extra = (
            "- Energía hoy: integración de la irradiancia solar desde las 00:00 hasta ahora, expresada en MJ/m²."
            if lang == "es"
            else "- Energy today: integration of solar irradiance from 00:00 until now, expressed in MJ/m²."
        )
        if text:
            return f"{text}\n{extra}"
        return (
            f"Radiación solar instantánea medida por piranómetro.\n{extra}"
            if lang == "es"
            else f"Instantaneous solar radiation measured by pyranometer.\n{extra}"
        )
    if normalized_title == "altura del sol":
        extra = (
            "- Culminación: altura máxima que alcanza el Sol ese día al pasar por el meridiano local."
            if lang == "es"
            else "- Culmination: maximum solar altitude reached that day when the Sun crosses the local meridian."
        )
        if text:
            return f"{text}\n{extra}"
        return (
            f"Altura angular del Sol sobre el horizonte.\n{extra}"
            if lang == "es"
            else f"Angular height of the Sun above the horizon.\n{extra}"
        )
    if not text:
        text = (
            FALLBACK_DEFINITIONS_ES.get(lookup)
            if lang == "es"
            else FALLBACK_DEFINITIONS_EN.get(lookup) or FALLBACK_DEFINITIONS_ES.get(lookup)
        )
    if text:
        return text
    return t("cards.tooltip_unavailable")


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
        return t("cards.tooltip_unavailable")
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
    tooltip_key: str = "",
) -> str:
    """
    Genera HTML de una tarjeta de dato meteorologico.
    """
    unit_html = f"<span class='unit'>{unit}</span>" if unit else ""
    sub_html = f"<div class='subtitle'>{subtitle_html}</div>" if subtitle_html else ""
    icon_html = icon_img(icon_kind, uid=uid, dark=dark)

    tip_text = _card_tooltip_text(title, tooltip_key=tooltip_key)
    tip_html = _tooltip_html(tip_text)

    side_col = f"""
    <div class=\"side-col\">{side_html}</div>
    """ if side_html else ""

    return html_clean(
        f"""
  <div class="card card-h">
    <div class="card-help-wrap" tabindex="0" aria-label="{escape(t('cards.help_aria', title=title))}">
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
