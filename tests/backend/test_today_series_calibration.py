"""
La serie del día también se calibra.

Las tarjetas de Observación y los gráficos de Tendencias salen del mismo
aparato: calibrar unas y otras no dejaba las dos pestañas contando cosas
distintas de la misma estación.
"""

from __future__ import annotations

from .test_observations_endpoint import WU_TODAY_SERIES_BODY


def _temps(client, calibration=None):
    payload = {"provider": "WU", "station_id": "ITEST123", "api_key": "fake"}
    if calibration:
        payload["calibration"] = calibration
    response = client.post("/v1/observations/series/today", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["temps"]


def test_today_series_applies_the_thermometer_offset(app_factory) -> None:
    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        plain = _temps(client)

    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        shifted = _temps(client, {"thermometer": 1.5})

    assert plain, "la serie de prueba debería traer temperaturas"
    assert shifted == [round(value + 1.5, 6) for value in plain]


def test_an_unknown_sensor_is_rejected(app_factory) -> None:
    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={
                "provider": "WU",
                "station_id": "ITEST123",
                "api_key": "fake",
                "calibration": {"altimeter": 1.0},
            },
        )

    assert response.status_code == 422
