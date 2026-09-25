"""La ficha que ve un buscador sale de lo guardado, no del proveedor.

Los rastreadores no consultan datos en vivo —cada URL del sitemap sería una
consulta con cuota—, y durante un tiempo eso dejó a Google viendo en cada
ficha un «vuelve a intentarlo» sin un solo valor. ``/snapshot`` le da la
última lectura que el almacén del ranking ya tiene.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from server.routers import observations as observations_router
from server.schemas.errors import ProviderError
from server.services import ranking


def _client(records, provider="METEOCAT"):
    store = ranking.RankingStore()
    store.replace_daily(provider, records)
    app = FastAPI()

    @app.exception_handler(ProviderError)
    async def _error(_: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response().model_dump())

    app.state.ranking_store = store
    app.include_router(observations_router.router, prefix="/v1")
    return TestClient(app)


def _record(at: datetime, **extra):
    base = dict(
        provider="METEOCAT", station_id="CG", name="Molló - Fabert",
        lat=42.38, lon=2.41, local_date=ranking.RankingStore.local_day("METEOCAT"),
    )
    base.update(extra)
    return ranking.StationDaily(**base)


def test_the_snapshot_has_the_shape_of_the_processed_observation():
    now = datetime.now(tz=timezone.utc)
    epoch = int(now.timestamp()) - 900
    record = _record(
        now, tcur=14.4, tcur_at=epoch, wind=12.0, wind_dir=370.0, wind_at=epoch - 600,
        tmax=26.5, tmin=13.6, gust=40.0, rain=1.2,
    )
    with _client([record]) as client:
        response = client.get("/v1/observations/snapshot", params={"provider": "meteocat", "station_id": "CG"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] is True
    assert body["observation"] == {
        "Tc": 14.4, "wind": 12.0, "wind_dir_deg": 10.0, "epoch": epoch, "precip_total": 1.2,
    }
    assert body["daily_extremes"] == {"temp_max": 26.5, "temp_min": 13.6, "gust_max": 40.0}
    assert body["derivatives"] == {}


def test_an_old_reading_is_not_served_as_current():
    now = datetime.now(tz=timezone.utc)
    stale = int((now - timedelta(hours=7)).timestamp())
    with _client([_record(now, tcur=3.0, tcur_at=stale, tmax=10.0)]) as client:
        response = client.get("/v1/observations/snapshot", params={"provider": "METEOCAT", "station_id": "CG"})
    assert response.status_code == 404


def test_an_unknown_station_is_a_404_and_never_calls_the_provider():
    now = datetime.now(tz=timezone.utc)
    with _client([_record(now, tcur=3.0, tcur_at=int(now.timestamp()))]) as client:
        response = client.get("/v1/observations/snapshot", params={"provider": "METEOCAT", "station_id": "XX"})
    assert response.status_code == 404
