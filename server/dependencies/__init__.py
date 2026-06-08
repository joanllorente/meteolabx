"""
Dependencies de FastAPI reutilizables.

Aquí van las funciones que se inyectan vía ``Depends(...)``: settings,
cliente HTTP compartido (``httpx.AsyncClient``), validación de API keys,
rate-limiter, etc.
"""
