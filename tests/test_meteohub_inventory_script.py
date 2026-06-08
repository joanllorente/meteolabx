from scripts import build_meteohub_inventory as inventory


def test_iter_station_records_reads_name_and_coordinates():
    payload = {
        "data": [
            {
                "stat": {
                    "lat": 42.4575,
                    "lon": 12.99972,
                    "net": "dpcn-lazio",
                    "details": [{"var": "B01019", "val": "Monte Terminillo"}],
                },
                "prod": [],
            }
        ]
    }

    records = list(inventory._iter_station_records(payload))

    assert records == [
        {
            "network": "dpcn-lazio",
            "lat": 42.4575,
            "lon": 12.99972,
            "name": "Monte Terminillo",
            "details": [{"var": "B01019", "val": "Monte Terminillo"}],
            "raw_stat": {
                "lat": 42.4575,
                "lon": 12.99972,
                "net": "dpcn-lazio",
                "details": [{"var": "B01019", "val": "Monte Terminillo"}],
            },
        }
    ]


def test_upsert_merges_products_into_single_station():
    stations = {}
    dataset_by_network = {
        "dpcn-lazio": {
            "name": "dpcn-lazio",
            "license": "CCBY4.0",
            "group_license": "CCBY_COMPLIANT",
            "attribution": "ItaliaMeteo",
        }
    }
    record = {
        "network": "dpcn-lazio",
        "lat": 42.4575,
        "lon": 12.99972,
        "name": "Monte Terminillo",
        "details": [{"var": "B01019", "val": "Monte Terminillo"}],
    }

    inventory._upsert_station(
        stations,
        record,
        inventory.ProductSpec("temperature", "B12101", "Temperature"),
        dataset_by_network,
        "2026-05-28",
        "2026-05-28",
    )
    inventory._upsert_station(
        stations,
        record,
        inventory.ProductSpec("wind_speed", "B11002", "Wind speed"),
        dataset_by_network,
        "2026-05-28",
        "2026-05-28",
    )

    finalized = inventory._finalize_stations(stations)

    assert len(finalized) == 1
    station = finalized[0]
    assert station["id"] == "dpcn-lazio|42.45750|12.99972|monte-terminillo"
    assert station["products"] == ["B11002", "B12101"]
    assert station["has_temperature"] is True
    assert station["has_wind_speed"] is True
    assert station["has_wind"] is True
    assert station["has_precipitation"] is False
