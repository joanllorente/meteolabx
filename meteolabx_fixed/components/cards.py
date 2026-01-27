"""
Componentes de tarjetas y grillas para visualización de datos
"""
import streamlit as st
from utils.helpers import html_clean
from .icons import icon_img


def card(title: str, value: str, unit: str = "", icon_kind: str = "temp", 
         subtitle_html: str = "", side_html: str = "", uid: str = "x", dark: bool = False) -> str:
    """
    Genera HTML de una tarjeta de dato meteorológico
    
    Args:
        title: Título de la tarjeta
        value: Valor principal a mostrar
        unit: Unidad de medida
        icon_kind: Tipo de icono
        subtitle_html: HTML adicional para el subtítulo
        uid: ID único
        dark: Tema oscuro
        
    Returns:
        String con HTML de la tarjeta
    """
    unit_html = f"<span class='unit'>{unit}</span>" if unit else ""
    sub_html = f"<div class='subtitle'>{subtitle_html}</div>" if subtitle_html else ""
    icon_html = icon_img(icon_kind, uid=uid, dark=dark)

    side_col = f"""
    <div class=\"side-col\">{side_html}</div>
    """ if side_html else ""


    return html_clean(f"""
  <div class="card card-h">
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
""")


def section_title(text: str):
    """
    Renderiza un título de sección
    
    Args:
        text: Texto del título
    """
    st.markdown(f"<div class='section-title'>{text}</div>", unsafe_allow_html=True)


def render_grid(cards: list, cols: int = 3, extra_class: str = ""):
    """
    Renderiza una grilla de tarjetas
    
    Args:
        cards: Lista de strings HTML de tarjetas
        cols: Número de columnas
        extra_class: Clase CSS adicional
    """
    cards_html = "".join(cards)
    html = f"<div class='grid grid-{cols} {extra_class}'>{cards_html}</div>"
    st.markdown(html, unsafe_allow_html=True)
