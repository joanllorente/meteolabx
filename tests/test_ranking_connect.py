from types import SimpleNamespace

import pytest

from tabs import ranking


class _QueryParams(dict):
    pass


def test_rank_connect_query_is_processed_outside_ranking_tab(monkeypatch):
    calls = []
    query_params = _QueryParams({"rank_connect": "AEMET~9434"})
    fake_st = SimpleNamespace(
        query_params=query_params,
        session_state={"active_tab": "observation"},
        rerun=lambda: (_ for _ in ()).throw(RuntimeError("rerun")),
    )
    monkeypatch.setattr(ranking, "st", fake_st)
    monkeypatch.setattr(
        ranking,
        "_cached_ranking",
        lambda providers, limit, order=None: {
            "metrics": {
                "tmax": [
                    {
                        "provider": "AEMET",
                        "station_id": "9434",
                        "name": "Zaragoza Aeropuerto",
                        "lat": 41.6606,
                        "lon": -1.0042,
                        "elevation_m": 249,
                        "station_tz": "Europe/Madrid",
                    }
                ],
            },
        },
    )

    with pytest.raises(RuntimeError, match="rerun"):
        ranking.handle_rank_connect_query({"apply_station_selection": lambda station, **kwargs: calls.append((station, kwargs))})

    assert "rank_connect" not in query_params
    assert calls == [
        (
            {
                "provider_id": "AEMET",
                "station_id": "9434",
                "name": "Zaragoza Aeropuerto",
                "lat": 41.6606,
                "lon": -1.0042,
                "elevation_m": 249,
                "station_tz": "Europe/Madrid",
            },
            {
                "connected": True,
                "pending_active_tab": "observation",
                "clear_runtime_cache": True,
            },
        )
    ]
