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
# Radio para matches por identificador fuerte (WMO/WIGOS/ICAO). Los partes
# synop/BUFR reportan coordenadas redondeadas (a veces al minuto) y el punto
# de referencia del aeropuerto puede estar a km del sensor: con id compartido
# el emparejamiento sigue siendo inequívoco a esta distancia.
IDENTIFIER_DISTANCE_M = 5_000.0
# Radio relajado para estaciones IEM de la red WMO_BUFR_SRF: nombres truncados
# a 20 chars en mayúsculas ("BARCELONA/JOSEP TARR") y coordenadas redondeadas,
# así que ni el nombre ni los 150 m generales funcionan. Caso testigo: AEMET
# 0076 "BARCELONA AEROPUERTO" ↔ WMO_BUFR 0-724-0-181 a ~830 m.
BUFR_NEAR_DISTANCE_M = 1_200.0
# Máximo de candidatos IEM conservados por estación oficial. IEM suele tener
# VARIOS duplicados de la misma estación física (p.ej. ES__ASOS|LEBL y
# WMO_BUFR_SRF|0-724-0-181 para el aeropuerto de Barcelona); quedarse solo
# con el mejor dejaba el resto escapar.
MAX_CANDIDATES_PER_SOURCE = 4

# ISO 3166-1 numérico por proveedor: los ids WIGOS de WMO_BUFR_SRF son
# ``0-{ISO numérico}-0-{nº nacional}`` (p.ej. 0-724-0-181 = España, synop
# 08181). Permite casar el id nacional con el WMO de los inventarios.
PROVIDER_ISO_NUMERIC = {
    "AEMET": 724, "METEOCAT": 724, "METEOGALICIA": 724, "EUSKALMET": 724,
    "POEM": 724, "METEOFRANCE": 250, "METEOHUB_IT": 380, "METOFFICE": 826,
    "FROST": 578,
}

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


def _wmo_int(value: Any) -> int | None:
    clean = _identifier(value)
    if clean and re.fullmatch(r"\d{1,6}", clean):
        return int(clean)
    return None


def _wigos_parts(value: Any) -> tuple[int, int] | None:
    """``0-{emisor}-0-{nº local}`` → (emisor, nº). Emisor 20000 = WMO global
    (el nº local es el synop de 5 dígitos); otro emisor = ISO numérico del
    país (el nº local es el identificador nacional, sin el bloque WMO)."""
    parts = str(value or "").strip().split("-")
    if len(parts) != 4:
        return None
    try:
        return int(parts[1]), int(parts[3])
    except ValueError:
        return None


def _strong_identifiers(raw: dict[str, Any], station_id: str, provider: str, network: str = "") -> set[str]:
    identifiers: set[str] = set()

    def add(namespace: str, value: Any) -> None:
        clean = _identifier(value)
        if clean:
            identifiers.add(f"{namespace}:{clean}")

    def add_wmo(value: Any) -> None:
        """WMO de 5 dígitos → id global + id nacional (emisor ISO + nº sin
        bloque). El nº nacional solo es fiable en países de bloque único
        (ES=08, FR=07, NO=01, IT=16, GB=03), que son justo los proveedores
        del catálogo; la distancia acota cualquier colisión residual."""
        number = _wmo_int(value)
        if number is None or number <= 0:
            return
        identifiers.add(f"WMO:{number}")
        issuer = PROVIDER_ISO_NUMERIC.get(provider)
        if issuer:
            identifiers.add(f"WMONAT:{issuer}:{number % 1000}")

    add_wmo(raw.get("id_omm"))
    add_wmo(raw.get("synop"))
    add_wmo(raw.get("wmo_id"))
    wigos = _wigos_parts(raw.get("wigos_id"))
    if wigos:
        issuer, local = wigos
        if issuer == 20000:
            add_wmo(local)
        else:
            identifiers.add(f"WMONAT:{issuer}:{local}")
    attributes = raw.get("attributes")
    if isinstance(attributes, dict):
        add_wmo(attributes.get("WMO_ID"))
        add("GHCN", attributes.get("GHCNH_ID"))
        add("NCEI", attributes.get("NCEI_ID"))
        add("ICAO", attributes.get("ICAO"))
    # IEM ASOS identifiers are ICAO codes. Short/numeric IDs are deliberately
    # excluded because values such as "24" collide across unrelated networks.
    clean_station_id = _identifier(station_id)
    if provider == "IEM" and re.fullmatch(r"[A-Z]{4}", clean_station_id):
        add("ICAO", clean_station_id)
    # El station_id de WMO_BUFR_SRF ES un id WIGOS (0-724-0-181).
    if provider == "IEM" and "WMO_BUFR" in str(network).upper():
        bufr = _wigos_parts(clean_station_id)
        if bufr:
            issuer, local = bufr
            if issuer == 20000:
                identifiers.add(f"WMO:{local}")
            else:
                identifiers.add(f"WMONAT:{issuer}:{local}")
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
    iem_network: str = "",
) -> tuple[str, float] | None:
    if shared_identifiers and distance_m <= IDENTIFIER_DISTANCE_M:
        return "secure", 0.99
    is_bufr = "WMO_BUFR" in str(iem_network).upper()
    if distance_m > (BUFR_NEAR_DISTANCE_M if is_bufr else NEAR_DISTANCE_M):
        return None
    elevation_compatible = elevation_delta_m is None or elevation_delta_m <= 50
    if distance_m <= 50 and name_similarity >= 0.55 and elevation_compatible:
        confidence = min(0.97, 0.86 + 0.08 * name_similarity + 0.03 * (1 - distance_m / 50))
        return "secure", confidence
    if distance_m <= NEAR_DISTANCE_M and name_similarity >= 0.55 and elevation_compatible:
        confidence = min(0.85, 0.66 + 0.14 * name_similarity + 0.05 * (1 - distance_m / 150))
        return "probable", confidence
    if distance_m <= 75 and name_similarity >= 0.35 and (
        elevation_delta_m is None or elevation_delta_m <= 20
    ):
        return "probable", min(0.78, 0.62 + 0.20 * name_similarity)
    if is_bufr:
        # Nombres BUFR truncados/mayúsculas y coordenadas redondeadas: el
        # nombre pesa poco y la distancia tolera más. El validador de
        # observaciones (validate_station_alias_observations.py) es quien
        # confirma o refuta antes de ocultar nada.
        strict_elevation = elevation_delta_m is None or elevation_delta_m <= 25
        if name_similarity >= 0.3 and strict_elevation:
            confidence = min(0.8, 0.6 + 0.15 * name_similarity + 0.1 * (1 - distance_m / BUFR_NEAR_DISTANCE_M))
            return "probable", confidence
        if distance_m <= 600 and strict_elevation:
            return "ambiguous", max(0.35, 0.55 - distance_m / 2000)
    if distance_m > NEAR_DISTANCE_M:
        return None
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
                shared = source_ids & _strong_identifiers(
                    iem_raw, iem["station_id"], "IEM", iem["network_code"],
                )
                is_bufr = "WMO_BUFR" in str(iem["network_code"]).upper()
                near_cutoff = BUFR_NEAR_DISTANCE_M if is_bufr else NEAR_DISTANCE_M
                if distance > near_cutoff and not shared:
                    continue
                name_score = _name_similarity(source["name"], iem["name"])
                elevation_delta = None
                if source["elevation_m"] is not None and iem["elevation_m"] is not None:
                    elevation_delta = abs(float(source["elevation_m"]) - float(iem["elevation_m"]))
                classification = _classify(
                    distance_m=distance, name_similarity=name_score,
                    elevation_delta_m=elevation_delta, shared_identifiers=shared,
                    iem_network=iem["network_code"],
                )
                if classification is None:
                    continue
                label, confidence = classification
                ranked.append((priority[label], -confidence, distance, iem, label,
                               confidence, name_score, elevation_delta, sorted(shared)))
            if not ranked:
                continue
            ranked.sort(key=lambda item: item[:3])
            # TODOS los candidatos plausibles (hasta el tope), no solo el
            # mejor: IEM suele duplicar la misma estación física en varias
            # redes (ASOS + WMO_BUFR_SRF) y quedarse con una dejaba escapar
            # el resto. Si dos oficiales reclaman la misma IEM, ambos alias
            # conviven y el validador de observaciones decide.
            for entry in ranked[:MAX_CANDIDATES_PER_SOURCE]:
                _, _, distance, iem, label, confidence, name_score, elevation_delta, shared = entry
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
