from scripts.refresh_aemet_station_inventory import _dms_to_decimal, build_inventory


def test_build_inventory_unites_active_and_climatological_stations():
    previous = [{
        "idema": "5000C", "nombre": "CEUTA", "online": False,
        "replacement_station_id": "5000D", "replacement_station_name": "CEUTA LOMA LARGA",
    }]
    current = [{
        "idema": "5000D", "ubi": "CEUTA LOMA LARGA", "lat": 35.89,
        "lon": -5.35, "alt": 90, "fint": "2026-09-06T12:00:00",
        "ta": 24.0,
    }]
    climate = [
        {
            "indicativo": "5000C", "nombre": "CEUTA", "provincia": "CEUTA",
            "latitud": "355319N", "longitud": "052049W", "altitud": "87",
            "indsinop": "60320",
        },
        {
            "indicativo": "5000D", "nombre": "CEUTA LOMA LARGA", "provincia": "CEUTA",
            "latitud": "355400N", "longitud": "052100W", "altitud": "90",
        },
    ]

    stations, report = build_inventory(current, climate, previous)
    by_code = {row["idema"]: row for row in stations}

    assert by_code["5000C"]["online"] is False
    assert by_code["5000C"]["historical_only"] is True
    assert by_code["5000C"]["replacement_station_id"] == "5000D"
    assert by_code["5000D"]["online"] is True
    assert by_code["5000D"]["sensors"]["thermometer"] is True
    assert report["added_active"] == ["5000D"]
    assert report["newly_historical"] == []


def test_aemet_sexagesimal_coordinates_are_converted():
    assert round(_dms_to_decimal("355319N"), 6) == 35.888611
    assert round(_dms_to_decimal("052049W"), 6) == -5.346944
