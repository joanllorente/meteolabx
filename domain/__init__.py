"""
Lógica meteorológica pura (sin Streamlit, sin FastAPI).

Este paquete contiene el dominio: funciones que toman datos y devuelven
datos. Es importable tanto desde el frontend Streamlit como desde el
backend FastAPI, lo que permite que el backend devuelva observaciones
ya procesadas (con derivadas termodinámicas, claridad del cielo, ET0,
tendencia de presión) sin duplicar lógica.

Garantías:
- Cero ``import streamlit`` / ``import st``.
- Cero ``import fastapi`` / ``import server``.
- Funciones puras donde sea posible; cuando hay estado, se pasa como
  parámetro explícito.
"""
