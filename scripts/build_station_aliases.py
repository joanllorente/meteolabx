#!/usr/bin/env python3
"""Populate conservative IEM duplicate candidates in the unified catalog."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "stations.sqlite"
DEFAULT_REPORT = ROOT / "data" / "station_alias_review_report.json"
NEAR_DISTANCE_M = 150.0
IDENTIFIER_DISTANCE_M = 1_000.0

# IEM labels WMO/BUFR records as UN even when their coordinates are inside the
# provider territory. Those records remain valid because the original catalog
# supplies the geographic anchor. NWS coverage is defined by its own catalog,
# which includes stations outside the continental US.
ALLOWED_IEM_COUNTRIES = {
    "AEMET": {"ES", "UN"},
    "EUSKALMET": {"ES", "UN"},
    "METEOCAT": {"ES", "UN"},
    "METEOGALICIA": {"ES", "UN"},
    "POEM": {"ES", "UN"},
    "METEOFRANCE": {
        "FR", "UN", "GF", "GP", "MQ", "RE", "YT", "PM", "BL", "MF",
        "NC", "PF", "WF", "TF", "DM", "KM",
    },
    "METEOHUB_IT": {"IT", "UN"},
    "METOFFICE": {"GB", "GG", "JE", "IM", "UN"},
    "FROST": {"NO", "SJ", "UN"},
    "NWS": None,
}


def _normal_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    text = re.sub(
        r"\b(station|weather|airport|aeroport|aeropuerto|meteo|automatic|automatica)\b",
        " ", text,
    )
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _name_similarity(left: str, right: str) -> float:
    a, b = _normal_name(left), _normal_name(right)
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def _identifier(value: Any) -> str:
    text = str(value or "").strip().upper()
    return "" if text in {"", "NONE", "NULL", "9999", "99999"} else text


def _strong_identifiers(raw: dict[str, Any], station_id: str, provider: str) -> set[str]:
    identifiers: set[str] = set()

    def add(namespace: str, value: Any) -> None:
        clean = _identifier(value)
        if clean:
            identifiers.add(f"{namespace}:{clean}")

    add("WMO", raw.get("id_omm"))
    add("WMO", raw.get("synop"))
    attributes = raw.get("attributes")
    if isinstance(attributes, dict):
        add("WMO", attributes.get("WMO_ID"))
        add("GHCN", attributes.get("GHCNH_ID"))
        add("NCEI", attributes.get("NCEI_ID"))
        add("ICAO", attributes.get("ICAO"))
    # IEM ASOS identifiers are ICAO codes. Short/numeric IDs are deliberately
    # excluded because values such as "24" collide across unrelated networks.
    clean_station_id = _identifier(station_id)
    if provider == "IEM" and re.fullmatch(r"[A-Z]{4}", clean_station_id):
        add("ICAO", clean_station_id)
    return identifiers


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 12_742_000.0 * math.asin(min(1.0, math.sqrt(value)))


def _classify(
    *, distance_m: float, name_similarity: float,
    elevation_delta_m: float | None, shared_identifiers: set[str],
) -> tuple[str, float] | None:
    if shared_identifiers and distance_m <= IDENTIFIER_DISTANCE_M:
        return "secure", 0.99
    if distance_m > NEAR_DISTANCE_M:
        return None
    elevation_compatible = elevation_delta_m is None or elevation_delta_m <= 50
    if distance_m <= 50 and name_similarity >= 0.55 and elevation_compatible:
        confidence = min(0.97, 0.86 + 0.08 * name_similarity + 0.03 * (1 - distance_m / 50))
        return "secure", confidence
    if name_similarity >= 0.55 and elevation_compatible:
        confidence = min(0.85, 0.66 + 0.14 * name_similarity + 0.05 * (1 - distance_m / 150))
        return "probable", confidence
    if distance_m <= 75 and name_similarity >= 0.35 and (
        elevation_delta_m is None or elevation_delta_m <= 20
    ):
        return "probable", min(0.78, 0.62 + 0.20 * name_similarity)
    confidence = max(0.30, min(0.60, 0.55 - distance_m / 1000 + name_similarity * 0.15))
    return "ambiguous", confidence


def _raw(raw_json: str) -> dict[str, Any]:
    value = json.loads(raw_json)
    return value if isinstance(value, dict) else {}


def _country_compatible(provider: str, iem_country: Any) -> bool:
    provider = str(provider).upper()
    if provider in ALLOWED_IEM_COUNTRIES and ALLOWED_IEM_COUNTRIES[provider] is None:
        return True
    allowed = ALLOWED_IEM_COUNTRIES.get(provider)
    country = str(iem_country or "").strip().upper()
    return bool(allowed and country in allowed)


def build_aliases(database_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    iem_candidate_counts: Counter[int] = Counter()
    geography_rejections = 0
    geography_rejection_groups: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {
        "secure": [], "probable": [], "ambiguous": [],
    }
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        sources = connection.execute(
            """
            SELECT s.*, r.raw_json
            FROM stations s
            JOIN station_inventory_records r ON r.record_pk = s.source_record_pk
            WHERE s.provider <> 'IEM'
              AND s.latitude IS NOT NULL AND s.longitude IS NOT NULL
            ORDER BY s.station_pk
            """
        ).fetchall()
        candidates_to_insert: list[tuple[Any, ...]] = []
        priority = {"secure": 0, "probable": 1, "ambiguous": 2}

        for source in sources:
            lat, lon = float(source["latitude"]), float(source["longitude"])
            latitude_delta = IDENTIFIER_DISTANCE_M / 110_574.0
            longitude_delta = IDENTIFIER_DISTANCE_M / (
                111_320.0 * max(0.01, abs(math.cos(math.radians(lat))))
            )
            nearby = connection.execute(
                """
                SELECT s.*, r.raw_json
                FROM station_rtree tree
                JOIN stations s USING(station_pk)
                JOIN station_inventory_records r ON r.record_pk = s.source_record_pk
                WHERE s.provider = 'IEM'
                  AND tree.min_latitude >= ? AND tree.max_latitude <= ?
                  AND tree.min_longitude >= ? AND tree.max_longitude <= ?
                """,
                (lat - latitude_delta, lat + latitude_delta,
                 lon - longitude_delta, lon + longitude_delta),
            ).fetchall()
            source_raw = _raw(source["raw_json"])
            source_ids = _strong_identifiers(source_raw, source["station_id"], source["provider"])
            ranked = []
            for iem in nearby:
                if not _country_compatible(source["provider"], iem["country"]):
                    geography_rejections += 1
                    geography_rejection_groups[
                        f"{source['provider']}:{str(iem['country'] or '(missing)').upper()}"
                    ] += 1
                    continue
                distance = _distance_m(lat, lon, float(iem["latitude"]), float(iem["longitude"]))
                iem_raw = _raw(iem["raw_json"])
                shared = source_ids & _strong_identifiers(iem_raw, iem["station_id"], "IEM")
                if distance > NEAR_DISTANCE_M and not shared:
                    continue
                name_score = _name_similarity(source["name"], iem["name"])
                elevation_delta = None
                if source["elevation_m"] is not None and iem["elevation_m"] is not None:
                    elevation_delta = abs(float(source["elevation_m"]) - float(iem["elevation_m"]))
                classification = _classify(
                    distance_m=distance, name_similarity=name_score,
                    elevation_delta_m=elevation_delta, shared_identifiers=shared,
                )
                if classification is None:
                    continue
                label, confidence = classification
                ranked.append((priority[label], -confidence, distance, iem, label,
                               confidence, name_score, elevation_delta, sorted(shared)))
            if not ranked:
                continue
            ranked.sort(key=lambda item: item[:3])
            _, _, distance, iem, label, confidence, name_score, elevation_delta, shared = ranked[0]
            evidence = {
                "classification": label,
                "distance_m": round(distance, 1),
                "name_similarity": round(name_score, 3),
                "elevation_delta_m": round(elevation_delta, 1) if elevation_delta is not None else None,
                "shared_identifiers": shared,
                "source": {
                    "provider": source["provider"], "network": source["network_code"],
                    "station_id": source["station_id"], "name": source["name"],
                },
                "iem": {
                    "network": iem["network_code"], "station_id": iem["station_id"],
                    "name": iem["name"], "country": iem["country"],
                },
                "observation_comparison": None,
            }
            candidates_to_insert.append((
                iem["station_pk"], source["station_pk"], confidence,
                f"inventory_{label}", json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
            ))
            counts[label] += 1
            provider_counts[source["provider"]] += 1
            iem_candidate_counts[int(iem["station_pk"])] += 1
            if len(examples[label]) < 10:
                examples[label].append(evidence)

        connection.execute(
            "DELETE FROM station_aliases WHERE reviewed = 0 AND method LIKE 'inventory_%'"
        )
        connection.executemany(
            """
            INSERT INTO station_aliases(
                station_pk, canonical_station_pk, confidence, method, evidence_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(station_pk, canonical_station_pk) DO UPDATE SET
                confidence = excluded.confidence,
                method = excluded.method,
                evidence_json = excluded.evidence_json
            WHERE station_aliases.reviewed = 0
              AND station_aliases.method LIKE 'inventory_%'
            """,
            candidates_to_insert,
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(f"SQLite validation failed: {integrity}, {foreign_keys}")
    finally:
        connection.close()

    report = {
        "method": {
            "near_distance_m": NEAR_DISTANCE_M,
            "strong_identifier_distance_m": IDENTIFIER_DISTANCE_M,
            "warning": "Candidates are unreviewed; no stations were merged or deleted.",
            "short_station_ids_are_evidence": False,
            "observation_comparison": "pending for probable and ambiguous candidates",
        },
        "candidates": sum(counts.values()),
        "classifications": dict(sorted(counts.items())),
        "providers": dict(sorted(provider_counts.items())),
        "geographically_incompatible_pairs_rejected": geography_rejections,
        "geographic_rejections_by_provider_country": dict(
            sorted(geography_rejection_groups.items())
        ),
        "iem_stations_with_multiple_original_candidates": sum(
            count > 1 for count in iem_candidate_counts.values()
        ),
        "examples": examples,
    }
    if report_path is not None:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = build_aliases(args.database, args.report)
    print(f"Saved {report['candidates']} unreviewed candidates to {args.database}")
    for label, count in report["classifications"].items():
        print(f"  {label:10} {count:6}")


if __name__ == "__main__":
    main()
