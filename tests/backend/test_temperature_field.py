from __future__ import annotations

import io
import math

import numpy as np
import pytest
from PIL import Image

from server.services import ranking
from server.services.temperature_field import (
    CELL_DEG,
    COLOR_SCALE_VERSION,
    COLOR_STOPS,
    FIELD_ALGORITHM_VERSION,
    FIELD_BBOX,
    LOCAL_SPATIAL_AGGREGATION_CELLS,
    SPATIAL_AGGREGATION_CELLS,
    _aggregate_station_points,
    _expand_land_mask_into_water,
    colorize,
    interpolate_grid,
    render_field_png,
    render_grid_png,
)
from server.services.precipitation_field import interpolate_precipitation_grid


def test_interpolate_grid_paints_around_stations_only():
    temp, mask = interpolate_grid([(40.0, -3.0, 20.0)])

    assert mask.any()
    rows, cols = temp.shape
    row = int((FIELD_BBOX[2] - 40.0) / CELL_DEG)
    col = int((-3.0 - FIELD_BBOX[1]) / CELL_DEG)
    assert mask[row, col]
    assert temp[row, col] == pytest.approx(20.0)
    # Lejos de la estación (otro hemisferio) queda transparente.
    assert not mask[int(rows * 0.9), int(cols * 0.9)]


def test_precipitation_field_uses_shorter_support_and_preserves_amount_order():
    amount, mask = interpolate_precipitation_grid([
        (40.4, -5.0, 0.0),
        (40.4, -2.0, 25.0),
    ])
    row = int((FIELD_BBOX[2] - 40.4) / CELL_DEG)
    dry_col = int((-5.0 - FIELD_BBOX[1]) / CELL_DEG)
    wet_col = int((-2.0 - FIELD_BBOX[1]) / CELL_DEG)

    assert mask[row, dry_col] < mask[row, wet_col]
    assert mask[row, wet_col] > 0
    assert amount[row, wet_col] > amount[row, dry_col]


def test_precipitation_field_preserves_an_isolated_local_downpour():
    amount, mask = interpolate_precipitation_grid([
        (46.0, 0.0, 0.0),
        (46.0, 0.8, 38.0),
        (46.0, 1.6, 0.0),
    ])
    row = int((FIELD_BBOX[2] - 46.0) / CELL_DEG)
    wet_col = int((0.8 - FIELD_BBOX[1]) / CELL_DEG)
    near_col = int((0.6 - FIELD_BBOX[1]) / CELL_DEG)
    far_col = int((0.0 - FIELD_BBOX[1]) / CELL_DEG)

    # El máximo ya no se diluye por las estaciones secas de alrededor.
    assert amount[row, wet_col] == pytest.approx(38.0, abs=0.1)
    assert amount[row, near_col] > 20.0
    # Sigue siendo un fenómeno local: a 80 km domina la estación seca.
    assert amount[row, far_col] < 1.0
    assert mask[row, wet_col] > 0.95


def test_interpolate_grid_blends_between_stations():
    temp, mask = interpolate_grid([(40.0, -3.0, 10.0), (40.0, -2.5, 30.0)])
    row = int((FIELD_BBOX[2] - 40.0) / CELL_DEG)
    col = int((-2.75 - FIELD_BBOX[1]) / CELL_DEG)
    assert mask[row, col]
    assert 10.0 < temp[row, col] < 30.0


def test_dense_distant_network_cannot_dilute_local_hotspot():
    local_hot = [
        (40.0, -3.0, 38.0),
        (40.0, -3.0, 39.0),
        (40.0, -3.0, 40.0),
    ]
    distant_cool = [(40.0, 87.0, 18.0)] * 500

    temp, mask = interpolate_grid(
        local_hot + distant_cool,
        cell_deg=1.0,
        radius_cells=100,
    )
    row = int((FIELD_BBOX[2] - 40.0) / 1.0)
    col = int((-3.0 - FIELD_BBOX[1]) / 1.0)

    assert mask[row, col]
    assert temp[row, col] == pytest.approx(39.0, abs=0.25)


def test_station_density_is_spatially_aggregated_with_median():
    aggregated = _aggregate_station_points(
        [
            (40.0, -3.0, 10.0),
            (41.0, -2.0, 30.0),
            (40.0, -1.0, 100.0),
        ],
        cell_deg=1.0,
    )

    assert len(aggregated) == 1
    assert aggregated[0][2] == 30.0


def test_local_aggregation_uses_finer_blocks_than_regional_field():
    points = [
        (41.35, 1.65, 28.0),
        (41.35, 1.85, 34.0),
        (41.15, 1.65, 29.0),
        (41.15, 1.85, 33.0),
    ]

    regional = _aggregate_station_points(
        points,
        cell_deg=0.1,
        block_size_cells=SPATIAL_AGGREGATION_CELLS,
    )
    local = _aggregate_station_points(
        points,
        cell_deg=0.1,
        block_size_cells=LOCAL_SPATIAL_AGGREGATION_CELLS,
    )

    assert len(local) > len(regional)
    assert max(value for _row, _col, value in local) == 34.0


def test_coherent_local_hot_cluster_survives_regional_median():
    points = [
        (41.35, 1.65, 34.0),
        (41.35, 1.75, 34.0),
        (41.25, 1.65, 34.0),
        (41.25, 1.75, 34.0),
        (41.15, 1.85, 28.0),
        (41.15, 1.95, 28.0),
        (41.05, 1.85, 28.0),
        (41.05, 1.95, 28.0),
        (41.15, 1.65, 28.0),
    ]

    temp, _mask = interpolate_grid(points)
    hot_row = int((FIELD_BBOX[2] - 41.35) / CELL_DEG)
    hot_col = int((1.65 - FIELD_BBOX[1]) / CELL_DEG)
    cool_row = int((FIELD_BBOX[2] - 41.05) / CELL_DEG)
    cool_col = int((1.95 - FIELD_BBOX[1]) / CELL_DEG)

    assert temp[hot_row, hot_col] > 32.0
    assert temp[cool_row, cool_col] < 29.0


def test_isolated_local_extreme_cannot_create_dense_network_rash():
    points = [
        (32.0, 4.0, 45.0),
        (32.2, 4.0, 34.0),
        (31.8, 4.0, 34.0),
        (32.0, 4.2, 34.0),
        (32.0, 3.8, 34.0),
    ]

    temp, _mask = interpolate_grid(points)
    row = int((FIELD_BBOX[2] - 32.0) / CELL_DEG)
    col = int((4.0 - FIELD_BBOX[1]) / CELL_DEG)

    # El extremo se conserva como una región amplia y moderada: no alcanza los
    # 45 °C de la estación ni queda borrado por las cuatro lecturas de 34 °C.
    assert 38.0 < temp[row, col] < 42.0


def test_moderate_outlier_in_normal_network_does_not_draw_a_bullseye():
    points = [
        (
            40.0 + row * 0.3,
            -4.0 + col * 0.3,
            34.0 if row == 2 and col == 2 else 30.0,
        )
        for row in range(5)
        for col in range(5)
    ]

    temp, _mask = interpolate_grid(points)
    center_row = int((FIELD_BBOX[2] - 40.6) / CELL_DEG)
    center_col = int((-3.4 - FIELD_BBOX[1]) / CELL_DEG)
    nearby_col = center_col + 3

    # Cuatro grados de microclima o altitud no crean una diana individual.
    assert temp[center_row, center_col] - temp[center_row, nearby_col] < 0.75


def test_isolated_legitimate_extreme_keeps_a_moderated_background_signature():
    # Tres estaciones caen en el mismo bloque regional de 0,4°. La mediana
    # protege el campo frente a valores espurios, pero la estación extrema no
    # debe desaparecer por completo (caso Furnace Creek).
    points = [
        (40.05, -3.05, 44.0),
        (40.35, -3.05, 36.0),
        (40.05, -2.75, 36.0),
    ]

    temp, _mask = interpolate_grid(points)
    row = int((FIELD_BBOX[2] - 40.05) / CELL_DEG)
    col = int((-3.05 - FIELD_BBOX[1]) / CELL_DEG)

    # Se ve una señal cálida clara y amplia, moderada respecto a los 44 °C.
    assert 38.5 < temp[row, col] < 42.0


def test_dense_network_does_not_draw_station_sized_temperature_spots():
    # Alternar pequeñas anomalías en una red densa reproduce los lunares que
    # aparecían por toda Europa y EE. UU. El detalle incoherente debe cancelarse.
    points = [
        (40.0 + row * 0.1, -4.0 + col * 0.1, 27.0 + ((row + col) % 2) * 6.0)
        for row in range(8)
        for col in range(8)
    ]

    temp, _mask = interpolate_grid(points)
    row = int((FIELD_BBOX[2] - 40.35) / CELL_DEG)
    col = int((-3.65 - FIELD_BBOX[1]) / CELL_DEG)
    window = temp[row - 4:row + 5, col - 4:col + 5]

    # El campo puede tener gradiente, pero no saltos puntuales de varios grados.
    assert float(np.max(window) - np.min(window)) < 1.5


def test_sparse_hot_extremes_form_a_continuous_regional_corridor():
    # Dos núcleos cálidos separados ~660 km entre una red regional más fresca:
    # el campo no debe dibujar dos ojos desconectados sobre fondo frío.
    cool = [
        (lat, lon, 33.0)
        for lat in (22.0, 26.0, 30.0, 34.0, 38.0)
        for lon in (-10.0, -6.0, 6.0, 10.0)
    ]
    points = cool + [(28.0, 0.0, 46.0), (34.0, 0.0, 42.0)]

    temp, _mask = interpolate_grid(points)
    peak_row = int((FIELD_BBOX[2] - 28.0) / CELL_DEG)
    middle_row = int((FIELD_BBOX[2] - 31.0) / CELL_DEG)
    col = int((0.0 - FIELD_BBOX[1]) / CELL_DEG)

    assert temp[middle_row, col] > 39.0
    assert temp[peak_row, col] - temp[middle_row, col] < 5.0


def test_sparse_hot_group_uses_medium_scale_between_stations():
    sparse_hot = [
        (40.0, -10.0, 39.0),
        (40.0, 10.0, 39.0),
    ]
    distant_cool = [(40.0, 80.0, 20.0)] * 300

    temp, mask = interpolate_grid(
        sparse_hot + distant_cool,
        cell_deg=1.0,
        radius_cells=100,
    )
    row = int((FIELD_BBOX[2] - 40.0) / 1.0)
    midpoint_col = int((0.0 - FIELD_BBOX[1]) / 1.0)

    assert temp[row, midpoint_col] > 35.0


def test_colorize_alpha_and_gradient():
    temp = np.array([[-50.0, 0.0, 50.0]])
    mask = np.array([[True, True, False]])
    rgba = colorize(temp, mask)

    assert rgba.shape == (1, 3, 4)
    assert rgba[0, 0, 3] == 255 and rgba[0, 1, 3] == 255
    assert rgba[0, 2, 3] == 0
    # Frío azulado (B > R), templado no.
    assert rgba[0, 0, 2] > rgba[0, 0, 0]


def test_temperature_color_scale_starts_at_minus_twenty_with_purple():
    assert FIELD_ALGORITHM_VERSION == 10
    assert COLOR_SCALE_VERSION == 2
    assert COLOR_STOPS[0] == (-20.0, (98, 22, 146))
    rgba = colorize(np.array([[-50.0, -20.0]]), np.array([[True, True]]))
    assert tuple(rgba[0, 0, :3]) == (98, 22, 146)
    assert tuple(rgba[0, 1, :3]) == (98, 22, 146)


def test_land_mask_extends_one_pixel_into_water():
    source = Image.new("L", (5, 5), 0)
    source.putpixel((2, 2), 255)

    expanded = _expand_land_mask_into_water(source)

    assert all(
        expanded.getpixel((x, y)) == 255
        for x in range(1, 4)
        for y in range(1, 4)
    )
    assert expanded.getpixel((0, 0)) == 0


def test_render_field_png_returns_png_bytes():
    png = render_field_png([(40.0, -3.0, 22.0)])
    assert png.startswith(b"\x89PNG")


def test_render_viewport_png_matches_high_resolution_island_coast():
    bounds = (2.0, 38.9, 4.0, 40.2)
    width, height = 800, 520
    temp, mask = interpolate_grid([(39.60, 2.90, 28.0)])

    png = render_grid_png(
        temp,
        mask,
        bounds=bounds,
        width=width,
        height=height,
    )
    image = Image.open(io.BytesIO(png)).convert("RGBA")

    assert image.size == (width, height)

    def _pixel(lon: float, lat: float) -> tuple[int, int]:
        west, south, east, north = bounds
        mercator = lambda value: math.log(
            math.tan(math.pi / 4.0 + math.radians(value) / 2.0)
        )
        x = round((lon - west) / (east - west) * (width - 1))
        y = round(
            (mercator(north) - mercator(lat))
            / (mercator(north) - mercator(south))
            * (height - 1)
        )
        return x, y

    # Centro de Mallorca pintado; mar al oeste completamente transparente.
    assert image.getpixel(_pixel(2.90, 39.60))[3] > 240
    assert image.getpixel(_pixel(2.05, 39.70))[3] == 0


def test_temperature_field_endpoint_accepts_viewport_parameters():
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routers import stations as stations_router

    now = datetime.now(tz=timezone.utc)
    store = ranking.RankingStore()
    store.replace_daily("IEM", [
        ranking.StationDaily(
            provider="IEM",
            station_id="ES|TEST",
            name="Test",
            lat=39.60,
            lon=2.90,
            tmax=29.0,
            tmin=24.0,
            tcur=28.0,
            tcur_at=int(now.timestamp()),
            wind=22.0,
            wind_dir=315.0,
            wind_at=int(now.timestamp()),
            rain_24h=7.4,
            rain_24h_at=int(now.timestamp()),
            local_date=now.date().isoformat(),
        ),
    ])
    store.mark_refreshed()
    app = FastAPI()
    app.state.ranking_store = store
    app.include_router(stations_router.router, prefix="/v1")

    with TestClient(app) as client:
        response = client.get(
            "/v1/stations/temperature-field.png",
            params={
                "west": 2.0,
                "south": 38.9,
                "east": 4.0,
                "north": 40.2,
                "width": 640,
                "height": 400,
            },
        )
        temperatures_response = client.get("/v1/stations/current-temperatures")
        winds_response = client.get("/v1/stations/current-winds")
        precipitation_response = client.get("/v1/stations/precipitations-24h")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(response.content)).size == (640, 400)
    assert temperatures_response.status_code == 200
    assert temperatures_response.json()["count"] == 1
    assert temperatures_response.json()["updated_at"] == store.updated_at.isoformat()
    assert winds_response.status_code == 200
    assert winds_response.json()["count"] == 1
    assert winds_response.json()["points"][0]["speed"] == pytest.approx(22.0)
    assert winds_response.json()["points"][0]["direction"] == pytest.approx(315.0)
    assert precipitation_response.status_code == 200
    assert precipitation_response.json()["count"] == 1
    assert precipitation_response.json()["points"][0]["amount"] == pytest.approx(7.4)


def test_store_collects_only_fresh_current_temperatures():
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    now_epoch = int(now.timestamp())
    store = ranking.RankingStore()
    store.replace_daily("IEM", [
        ranking.StationDaily(
            provider="IEM", station_id="N|A", name="A",
            lat=40.0, lon=-3.0, tmax=25.0, tcur=21.5,
            tcur_at=now_epoch - 600, local_date="2026-07-13",
        ),
        ranking.StationDaily(
            provider="IEM", station_id="N|B", name="B",
            lat=41.0, lon=-3.5, tmax=24.0, tcur=None, local_date="2026-07-13",
        ),
        # Lectura colgada de hace 5 horas (estación parada): fuera.
        ranking.StationDaily(
            provider="IEM", station_id="N|C", name="C",
            lat=42.0, lon=-4.0, tmax=22.0, tcur=12.0,
            tcur_at=now_epoch - 5 * 3600, local_date="2026-07-13",
        ),
        # Instantánea sin timestamp: fuera (no se puede garantizar frescura).
        ranking.StationDaily(
            provider="IEM", station_id="N|D", name="D",
            lat=43.0, lon=-4.5, tmax=22.0, tcur=18.0,
            tcur_at=None, local_date="2026-07-13",
        ),
    ])

    points = store.current_temperature_points(now=now)
    assert points == [(40.0, -3.0, 21.5)]


def test_store_collects_only_fresh_complete_wind_vectors():
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    now_epoch = int(now.timestamp())
    store = ranking.RankingStore()
    store.replace_daily("IEM", [
        ranking.StationDaily(
            provider="IEM", station_id="N|A", name="A",
            lat=40.0, lon=-3.0, wind=18.5, wind_dir=370.0,
            wind_at=now_epoch - 600, local_date=now.date().isoformat(),
        ),
        ranking.StationDaily(
            provider="IEM", station_id="N|B", name="B",
            lat=41.0, lon=-3.5, wind=22.0, wind_dir=None,
            wind_at=now_epoch - 600, local_date=now.date().isoformat(),
        ),
        ranking.StationDaily(
            provider="IEM", station_id="N|C", name="C",
            lat=42.0, lon=-4.0, wind=30.0, wind_dir=90.0,
            wind_at=now_epoch - 5 * 3600, local_date=now.date().isoformat(),
        ),
    ])

    records = store.current_wind_records(now=now)

    assert len(records) == 1
    assert records[0].station_id == "N|A"
    assert records[0].wind == pytest.approx(18.5)
    assert records[0].wind_dir == pytest.approx(10.0)
    assert store.current_wind_points(now=now) == [(40.0, -3.0, 18.5)]


def test_meteocat_map_points_survive_two_hour_refresh_cadence():
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    observed_at = int(now.timestamp()) - 3 * 3600
    store = ranking.RankingStore()
    store.replace_daily("METEOCAT", [
        ranking.StationDaily(
            provider="METEOCAT", station_id="X1", name="Meteocat",
            lat=41.5, lon=2.1, tcur=18.0, tcur_at=observed_at,
            wind=12.0, wind_dir=90.0, wind_at=observed_at,
            rain_24h=4.2, rain_24h_at=observed_at,
            local_date=now.date().isoformat(),
        ),
        ranking.StationDaily(
            provider="METEOCAT", station_id="X2", name="Meteocat antigua",
            lat=42.0, lon=2.5, tcur=12.0,
            tcur_at=int(now.timestamp()) - 5 * 3600,
            local_date=now.date().isoformat(),
        ),
    ])

    assert store.current_temperature_points(now=now) == [(41.5, 2.1, 18.0)]
    assert store.current_wind_points(now=now) == [(41.5, 2.1, 12.0)]
    assert store.current_precipitation_points(now=now) == [(41.5, 2.1, 4.2)]


def test_store_rolling_rain_crosses_midnight_and_requires_full_coverage():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    store = ranking.RankingStore()
    for hours_ago in range(0, 24):
        observed = now - timedelta(hours=hours_ago)
        local = observed.astimezone(ZoneInfo("Europe/Madrid"))
        store.upsert_hourly(
            "AEMET", "TEST", day=local.date().isoformat(),
            hour_key=local.strftime("%Y-%m-%dT%H"), name="Test", locality="",
            lat=40.0, lon=-3.0,
            values={"rain": 0.5, "rain_at": int(observed.timestamp())},
        )

    records = store.reduce_accumulable_records("AEMET", now=now)
    assert records
    assert records[0].rain_24h == pytest.approx(12.0)
    assert records[0].rain_24h_at == int(now.timestamp())

    store.commit({"AEMET": records}, now=now)
    assert store.current_precipitation_points(now=now) == [(40.0, -3.0, 12.0)]


def test_store_excludes_catalog_hidden_duplicate_from_temperature_field(monkeypatch):
    from datetime import datetime, timezone

    from server.services import stations as stations_service

    now = datetime.now(tz=timezone.utc)
    current_epoch = int(now.timestamp())
    store = ranking.RankingStore()
    store.replace_daily("METEOFRANCE", [
        ranking.StationDaily(
            provider="METEOFRANCE", station_id="98404004", name="CROZET",
            lat=-46.4325, lon=51.856667, tcur=5.5,
            tcur_at=current_epoch, local_date=now.date().isoformat(),
        ),
    ])
    store.replace_daily("IEM", [
        ranking.StationDaily(
            provider="IEM", station_id="WMO_BUFR_SRF|0-262-0-997",
            name="CROZET", lat=-46.4325, lon=51.8567, tcur=3.2,
            tcur_at=current_epoch, local_date=now.date().isoformat(),
        ),
    ])
    monkeypatch.setattr(
        stations_service,
        "is_station_hidden",
        lambda provider, station_id: (
            provider, station_id
        ) == ("IEM", "WMO_BUFR_SRF|0-262-0-997"),
    )

    records = store.current_temperature_records(now=now)

    assert [(record.provider, record.station_id) for record in records] == [
        ("METEOFRANCE", "98404004"),
    ]


def test_accumulable_reduce_keeps_latest_tcur_and_timestamp():
    store = ranking.RankingStore()
    day = store.local_day("AEMET")
    for hour, value, epoch in (("T08", 15.0, 1_000), ("T12", 24.0, 2_000)):
        store.upsert_hourly(
            "AEMET", "0201X", day=day, hour_key=f"{day}{hour}",
            name="X", locality="", lat=41.0, lon=2.0,
            values={
                "tmax": value, "tmin": value, "gust": None, "rain": None,
                "tcur": value, "tcur_at": epoch,
            },
        )

    records = store.reduce_accumulable_records("AEMET")
    assert len(records) == 1
    assert records[0].tcur == pytest.approx(24.0)
    assert records[0].tcur_at == 2_000
