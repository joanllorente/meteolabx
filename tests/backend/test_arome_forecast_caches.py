"""Las cachés de mapas de la API no caducan con el tiempo, solo por tamaño."""
from server.services import arome_forecast as af


def _usar(funcion, veces=1):
    for i in range(veces):
        funcion('token', f'producto{i}', '2026-09-23T12:00:00Z')


def test_an_untouched_cache_is_emptied_on_the_next_round(monkeypatch):
    """Doce cálculos son ~222 MB de matrices de viento residentes.

    De madrugada nadie los va a volver a pedir y la meseta se paga por minuto.
    """
    llamadas = []
    monkeypatch.setattr(af, '_USOS_PREVIOS', {})

    @af.lru_cache(maxsize=12)
    def calculo(token, producto, hora):
        llamadas.append(producto)
        return producto
    monkeypatch.setattr(af, '_CACHES_DE_MAPAS', (('cálculos', lambda: calculo),))

    _usar(calculo, 3)
    # Primera vuelta: se acaban de usar, se conservan.
    assert af.release_idle_frame_caches() == {}
    assert calculo.cache_info().currsize == 3
    # Segunda vuelta sin que nadie pregunte: fuera.
    assert af.release_idle_frame_caches() == {'cálculos': 3}
    assert calculo.cache_info().currsize == 0


def test_a_cache_in_use_is_left_alone(monkeypatch):
    monkeypatch.setattr(af, '_USOS_PREVIOS', {})

    @af.lru_cache(maxsize=12)
    def calculo(token, producto, hora):
        return producto
    monkeypatch.setattr(af, '_CACHES_DE_MAPAS', (('cálculos', lambda: calculo),))

    _usar(calculo, 2)
    af.release_idle_frame_caches()
    _usar(calculo, 2)  # alguien mirando el visor entre dos vueltas
    assert af.release_idle_frame_caches() == {}
    assert calculo.cache_info().currsize == 2


def test_surface_wind_goes_with_the_computed_frames(monkeypatch):
    """Sus campos los comparte el último cálculo: solo, no le sirve a nadie."""
    monkeypatch.setattr(af, '_USOS_PREVIOS', {})

    @af.lru_cache(maxsize=12)
    def calculo(token, producto, hora):
        return producto
    monkeypatch.setattr(af, '_CACHES_DE_MAPAS', (('cálculos', lambda: calculo),))
    af.release_idle_frame_caches()  # primera vuelta: aún no hay con qué comparar
    af._SURFACE_WIND_CACHE[('run', 'hora')] = ('u', 'v')
    try:
        assert af.release_idle_frame_caches() == {'viento en superficie': 1}
        assert not af._SURFACE_WIND_CACHE
    finally:
        af._SURFACE_WIND_CACHE.clear()


def test_the_real_caches_are_the_ones_wired_up():
    """Si alguna se renombra, que falle aquí y no en producción en silencio."""
    nombres = {nombre for nombre, _ in af._CACHES_DE_MAPAS}
    assert nombres == {'cálculos', 'mapas PNG', 'rejillas', 'perfiles convectivos'}
    for _, obtener in af._CACHES_DE_MAPAS:
        assert hasattr(obtener(), 'cache_clear')
