"""Persistencia de frames AROME precalculados, local o en Railway Buckets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import gzip
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Any, Protocol


DERIVED_FORECAST_PRODUCTS = (
    # Sale de dos niveles de IP1, no de una cobertura propia: va con los
    # derivados para que el paquete ya esté bajado cuando le toque.
    "vertical-totals",
    # Los convectivos tienen que estar todos aquí: NATIVE_PRODUCTS se define
    # como «lo que no es derivado», así que faltar en esta lista los encola
    # además en el nivel 0, donde cada uno recalcula el perfil entero con el
    # límite de los campos nativos.
    "vv-lfc",
    "updraft-helicity",
    "mslp-theta-e-850",
    "shear-01",
    "shear-03",
    "shear-06",
    "ebwd",
    "accumulated-precip",
    "mucape-muli",
    "mlcape-mlli",
    "sbcape-sbli",
    "dcape",
    "ordinary-cell-motion",
    "ship",
    "srh-01",
    "srh-03",
    "esrh",
    "scp",
    "stp",
)

PERSISTED_FORECAST_PRODUCTS = (
    "temperature-2m",
    "temperature-850",
    "temperature-500",
    "wind-level",
    "wind-gust",
    "shear-01",
    "shear-03",
    "shear-06",
    "ebwd",
    "precip-1h",
    "accumulated-precip",
    "relative-humidity-700",
    "shortwave-down",
    "reflectivity",
    "mu-ecape",
    "ml-ecape",
    "mucape-muli",
    "mlcape-mlli",
    "sbcape-sbli",
    "dcape",
    "ordinary-cell-motion",
    "ship",
    "srh-01",
    "srh-03",
    "esrh",
    "scp",
    "stp",
    "vv-lfc",
    "updraft-helicity",
    "mslp-theta-e-850",
    "cloud-cover",
    "vertical-totals",
)

CONVECTIVE_FORECAST_PRODUCTS = (
    # Orden de publicación por dependencias: primero los diagnósticos de
    # parcela, después los campos que los consumen y SHIP siempre al final.
    "mucape-muli",
    "mlcape-mlli",
    "sbcape-sbli",
    "dcape",
    "ordinary-cell-motion",
    "ebwd",
    # Cinemáticos: no dependen de las parcelas, sólo del perfil de viento.
    "srh-01",
    "vv-lfc",
    "updraft-helicity",
    "srh-03",
    "esrh",
    "scp",
    "stp",
    "ship",
)

# Diagnósticos cuyo horizonte se recorta: una hora convectiva cuesta minutos
# y una nativa segundos, así que se calculan menos plazos de ellos.
# Indices que salen de dos niveles isobaricos en vez de un perfil entero. No
# pasan por el nivel 2, pero su paquete solo se adelanta hasta el horizonte de
# los diagnosticos, asi que se recortan igual.
LEVEL_INDEX_PRODUCTS = ("vertical-totals",)

CAPPED_FORECAST_PRODUCTS = tuple(
    product
    for product in PERSISTED_FORECAST_PRODUCTS
    if product.startswith("shear-")
    or product in CONVECTIVE_FORECAST_PRODUCTS
    or product in LEVEL_INDEX_PRODUCTS
)


# Un solo mapa para empezar: geopotencial de 500 hPa en color con la presión
# al nivel del mar en isobaras. Es el mapa sinóptico de referencia y sale de
# dos mensajes GRIB por plazo, así que sirve para medir el coste real del
# modelo antes de ampliarlo.
ECMWF_FORECAST_PRODUCTS = ("z500-mslp",)

# Qué publica cada modelo. El almacén deja de asumir que todo lo que hay en el
# volumen es AROME: las claves y los manifiestos van por modelo, y así una
# pasada de ECMWF no puede pisar ni contarse dentro de otra.
FORECAST_MODEL_PRODUCTS: dict[str, tuple[str, ...]] = {
    "arome": PERSISTED_FORECAST_PRODUCTS,
    "ecmwf": ECMWF_FORECAST_PRODUCTS,
}
DEFAULT_FORECAST_MODEL = "arome"


# Revisión científica independiente del formato binario. Solo invalida los
# productos que cambian: los demás conservan sus frames y su disponibilidad.
AROME_CALCULATION_REVISION = 1
REVISED_AROME_PRODUCTS = frozenset({
    "mucape-muli", "mlcape-mlli", "sbcape-sbli", "ebwd", "ship",
    "ordinary-cell-motion", "srh-01", "srh-03", "dcape", "vv-lfc",
    "relative-humidity-700", "cloud-cover", "accumulated-precip", "vertical-totals",
})


def _upgrade_calculation_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if ("run" not in payload or "products" not in payload
            or payload.get("forecast_model", "arome") != "arome"
            or payload.get("calculation_revision") == AROME_CALCULATION_REVISION):
        return payload
    removed = 0
    for product in REVISED_AROME_PRODUCTS:
        state = payload["products"].get(product)
        if state is not None:
            removed += len(state.get("available_times", ()))
            state["available_times"] = []
            state["errors"] = {}
    progress = payload.get("progress")
    if progress:
        progress["frames_available"] = max(0, progress.get("frames_available", 0) - removed)
        total = progress.get("frames_total", 0)
        progress["percent"] = 100 * progress["frames_available"] / total if total else 0.
        progress["active_jobs"] = []
        progress["current_job"] = None
        progress["last_completed"] = None
        progress["error_count"] = sum(len(state.get("errors", {})) for state in payload["products"].values())
    payload["status"] = "publishing"
    payload["calculation_revision"] = AROME_CALCULATION_REVISION
    return payload


_SAFE_KEY = re.compile(r"^[A-Za-z0-9._/-]+$")


class ObjectStore(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


def _validate_key(key: str) -> str:
    value = str(key).strip().lstrip("/")
    if not value or not _SAFE_KEY.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("Clave de almacenamiento de predicción no válida.")
    return value


def forecast_models() -> tuple[str, ...]:
    """Modelos que el almacén sabe separar, en orden de publicación."""
    return tuple(FORECAST_MODEL_PRODUCTS)


def persisted_products(model: str = DEFAULT_FORECAST_MODEL) -> tuple[str, ...]:
    """Productos que ese modelo persiste; vacío si el modelo no existe."""
    return FORECAST_MODEL_PRODUCTS.get(_validate_model(model), ())


def _validate_model(model: str | None) -> str:
    name = str(model or DEFAULT_FORECAST_MODEL).strip().lower()
    if name not in FORECAST_MODEL_PRODUCTS:
        raise ValueError(f"El modelo de predicción «{model}» no está registrado.")
    return name


def _model_prefix(model: str | None) -> str:
    """Trozo de clave que separa un modelo de otro.

    AROME se queda sin prefijo a propósito: el volumen de producción ya tiene
    sus pasadas escritas en `forecast/runs/...`, y moverlas las dejaría
    huérfanas —invisibles para el visor y, peor, fuera del alcance de la poda,
    que es lo único que impide que el volumen se llene.
    """
    name = _validate_model(model)
    return "" if name == DEFAULT_FORECAST_MODEL else f"models/{name}/"


def latest_manifest_key(model: str = DEFAULT_FORECAST_MODEL) -> str:
    return f"forecast/{_model_prefix(model)}manifests/latest.json"


def run_slots_key(model: str = DEFAULT_FORECAST_MODEL) -> str:
    return f"forecast/{_model_prefix(model)}manifests/slots.json"


# Se conservan como constantes porque media base de código las importa; son
# las de AROME, que es el modelo sin prefijo.
LATEST_MANIFEST_KEY = latest_manifest_key()
RUN_SLOTS_KEY = run_slots_key()


@dataclass(frozen=True)
class LocalObjectStore:
    root: Path

    def _path(self, key: str) -> Path:
        return self.root / _validate_key(key)

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def delete_prefix(self, prefix: str) -> None:
        root = self._path(prefix)
        if root.is_file():
            root.unlink()
            return
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()


class S3ObjectStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        url_style: str = "virtual",
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depende del entorno Railway
            raise RuntimeError("Falta boto3 para usar Railway Storage Buckets.") from exc
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(s3={"addressing_style": url_style}),
        )

    def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=_validate_key(key))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return response["Body"].read()

    def put(self, key: str, content: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=_validate_key(key),
            Body=content,
            ContentType=content_type,
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_validate_key(key))

    def delete_prefix(self, prefix: str) -> None:
        safe_prefix = _validate_key(prefix).rstrip("/") + "/"
        while True:
            response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=safe_prefix)
            objects = [{"Key": item["Key"]} for item in response.get("Contents", ())]
            if not objects:
                break
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": objects, "Quiet": True},
            )


def _environment(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


@lru_cache(maxsize=1)
def get_forecast_store() -> ObjectStore:
    """Construye el Bucket Railway si está configurado; si no, usa disco."""
    bucket = _environment("AWS_S3_BUCKET_NAME", "BUCKET", "METEOLABX_FORECAST_BUCKET")
    endpoint = _environment("AWS_ENDPOINT_URL", "ENDPOINT", "METEOLABX_FORECAST_S3_ENDPOINT")
    access_key = _environment("AWS_ACCESS_KEY_ID", "ACCESS_KEY_ID")
    secret_key = _environment("AWS_SECRET_ACCESS_KEY", "SECRET_ACCESS_KEY")
    if bucket and endpoint and access_key and secret_key:
        return S3ObjectStore(
            bucket=bucket,
            endpoint=endpoint,
            access_key_id=access_key,
            secret_access_key=secret_key,
            region=_environment("AWS_DEFAULT_REGION", "REGION") or "auto",
            url_style=_environment("AWS_S3_URL_STYLE") or "virtual",
        )

    configured = _environment("METEOLABX_FORECAST_STORE_PATH")
    volume = _environment("RAILWAY_VOLUME_MOUNT_PATH")
    root = Path(configured or (Path(volume) / "forecast" if volume else "data/forecast_store"))
    return LocalObjectStore(root.resolve())


def run_slug(run_iso: str) -> str:
    value = datetime.fromisoformat(run_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return value.strftime("%Y%m%dT%H%M%SZ")


def valid_slug(valid_iso: str) -> str:
    return run_slug(valid_iso)


def frame_key(
    run_iso: str,
    product: str,
    valid_iso: str,
    *,
    scope: str = "model",
    vertical_kind: str | None = None,
    level: float | None = None,
    model: str = DEFAULT_FORECAST_MODEL,
) -> str:
    model_prefix = _model_prefix(model)
    scope_prefix = "" if scope == "model" else f"scopes/{_validate_key(scope)}/"
    product_slug = _validate_key(product)
    if _validate_model(model) == "arome" and product in REVISED_AROME_PRODUCTS:
        product_slug += f"--calc{AROME_CALCULATION_REVISION}"
    if product == "wind-level":
        kind = "height" if vertical_kind not in {"height", "isobaric"} else vertical_kind
        numeric_level = 10.0 if level is None else float(level)
        level_slug = f"{numeric_level:g}".replace(".", "p")
        product_slug = f"{product_slug}--{kind}--{level_slug}"
    return (
        f"forecast/{model_prefix}{scope_prefix}runs/{run_slug(run_iso)}"
        f"/{product_slug}/{valid_slug(valid_iso)}.grid.gz"
    )


def run_manifest_key(run_iso: str, *, model: str = DEFAULT_FORECAST_MODEL) -> str:
    return f"forecast/{_model_prefix(model)}manifests/{run_slug(run_iso)}.json"


def manifest_model(manifest: dict[str, Any] | None) -> str:
    """Modelo de un manifiesto. Los escritos antes de separarlos son AROME."""
    return _validate_model((manifest or {}).get("forecast_model"))


def read_json(store: ObjectStore, key: str) -> dict[str, Any] | None:
    content = store.get(key)
    return _upgrade_calculation_manifest(json.loads(content)) if content is not None else None


def write_json(store: ObjectStore, key: str, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    store.put(key, content, "application/json")


def write_grid(store: ObjectStore, key: str, content: bytes) -> None:
    store.put(key, gzip.compress(content, compresslevel=5), "application/gzip")


def read_compressed_grid(store: ObjectStore, key: str) -> bytes | None:
    """Devuelve el gzip persistido sin inflarlo en el servidor web."""
    return store.get(key)


def read_grid(store: ObjectStore, key: str) -> bytes | None:
    content = read_compressed_grid(store, key)
    return gzip.decompress(content) if content is not None else None


def grid_metadata(content: bytes) -> dict[str, Any]:
    if len(content) < 4:
        raise ValueError("Frame AROME persistido incompleto.")
    header_length = struct.unpack("<I", content[:4])[0]
    return json.loads(content[4:4 + header_length])


def new_manifest(
    run_iso: str,
    expected_times: list[str],
    *,
    scope: str = "model",
    catalog_products: dict[str, Any] | None = None,
    model: str = DEFAULT_FORECAST_MODEL,
    model_label: str = "",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    etiquetas = {"arome": "AROME France 0,025°", "ecmwf": "ECMWF IFS 0,25°"}
    nombre = _validate_model(model)
    return {
        "version": 1,
        # `model` era el rótulo que lee el visor y ahora convive con el
        # identificador que separa las claves: uno se enseña, el otro se
        # escribe en el volumen.
        "model": model_label or etiquetas.get(nombre, nombre.upper()),
        "forecast_model": nombre,
        "calculation_revision": AROME_CALCULATION_REVISION if nombre == "arome" else 0,
        "run": run_iso,
        "calculation_scope": scope,
        "status": "publishing",
        "created_at": now,
        "updated_at": now,
        "expected_times": sorted(set(expected_times)),
        "catalog_products": catalog_products or {},
        "products": {
            product: {"available_times": [], "errors": {}}
            for product in persisted_products(nombre)
        },
        "progress": {
            "frames_available": 0,
            "frames_total": 0,
            "percent": 0.0,
            "error_count": 0,
            "current_job": None,
            "active_jobs": [],
            "last_completed": None,
        },
    }


def cycle_slot(run_iso: str) -> str:
    hour = datetime.fromisoformat(run_iso.replace("Z", "+00:00")).astimezone(timezone.utc).hour
    if hour not in {0, 6, 12, 18}:
        raise ValueError("El RUN no pertenece a un turno principal 00/06/12/18Z.")
    return f"{hour:02d}"


def register_run_slot(store: ObjectStore, manifest: dict[str, Any]) -> str | None:
    """Publica el RUN en su turno 00/06/12/18 y devuelve el sustituido."""
    run_iso = str(manifest["run"])
    model = manifest_model(manifest)
    slot = cycle_slot(run_iso)
    slots_key = run_slots_key(model)
    index = read_json(store, slots_key) or {"version": 1, "slots": {}}
    previous = (index.get("slots", {}).get(slot) or {}).get("run")
    index.setdefault("slots", {})[slot] = {
        "run": run_iso,
        "manifest": run_manifest_key(run_iso, model=model),
        "updated_at": manifest.get("updated_at"),
    }
    write_json(store, slots_key, index)
    return str(previous) if previous and previous != run_iso else None


def retained_run_limit() -> int:
    """Pasadas que se conservan en el volumen."""
    try:
        return max(1, int(os.getenv("METEOLABX_FORECAST_RETAINED_RUNS", "3")))
    except ValueError:
        return 3


def prune_retained_runs(
    store: ObjectStore,
    keep: int | None = None,
    *,
    model: str = DEFAULT_FORECAST_MODEL,
) -> list[str]:
    """Deja solo las pasadas más recientes y borra las demás del volumen.

    Cada pasada ocupa más de un gigabyte, así que retener las cuatro del día
    desbordaba el volumen y el worker se quedaba sin poder escribir.
    """
    limit = retained_run_limit() if keep is None else max(1, keep)
    slots_key = run_slots_key(model)
    index = read_json(store, slots_key) or {}
    slots = dict(index.get("slots") or {})
    ordered = sorted(
        (
            (str(item.get("run")), slot)
            for slot, item in slots.items()
            if item.get("run")
        ),
        reverse=True,
    )
    removed: list[str] = []
    for run_iso, slot in ordered[limit:]:
        manifest = read_json(store, run_manifest_key(run_iso, model=model)) or {}
        delete_run(
            store,
            run_iso,
            scope=str(manifest.get("calculation_scope", "model")),
            model=model,
        )
        slots.pop(slot, None)
        removed.append(run_iso)
    if removed:
        index["slots"] = slots
        write_json(store, slots_key, index)
    return removed


def retained_manifests(
    store: ObjectStore, *, model: str = DEFAULT_FORECAST_MODEL
) -> list[dict[str, Any]]:
    index = read_json(store, run_slots_key(model)) or {}
    manifests = []
    for item in (index.get("slots") or {}).values():
        manifest = read_json(store, str(item.get("manifest", "")))
        if manifest:
            manifests.append(manifest)
    return sorted(manifests, key=lambda item: str(item.get("run", "")), reverse=True)


def delete_run(
    store: ObjectStore,
    run_iso: str,
    *,
    scope: str = "model",
    model: str = DEFAULT_FORECAST_MODEL,
) -> None:
    slug = run_slug(run_iso)
    base = f"forecast/{_model_prefix(model)}"
    prefix = (
        f"{base}runs/{slug}"
        if scope == "model"
        else f"{base}scopes/{_validate_key(scope)}/runs/{slug}"
    )
    store.delete_prefix(prefix)
    store.delete(run_manifest_key(run_iso, model=model))


def mark_available(manifest: dict[str, Any], product: str, valid_iso: str) -> None:
    product_state = manifest.setdefault("products", {}).setdefault(
        product, {"available_times": [], "errors": {}}
    )
    product_state["available_times"] = sorted(
        set(product_state.get("available_times", ())) | {valid_iso}
    )
    product_state.setdefault("errors", {}).pop(valid_iso, None)
    product_state.setdefault("retry_after", {}).pop(valid_iso, None)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def mark_error(
    manifest: dict[str, Any],
    product: str,
    valid_iso: str,
    message: str,
    *,
    retry_after: str | None = None,
) -> None:
    product_state = manifest.setdefault("products", {}).setdefault(
        product, {"available_times": [], "errors": {}}
    )
    product_state.setdefault("errors", {})[valid_iso] = str(message)[:500]
    attempts = product_state.setdefault("attempts", {})
    attempts[valid_iso] = int(attempts.get(valid_iso, 0)) + 1
    if retry_after:
        product_state.setdefault("retry_after", {})[valid_iso] = retry_after
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def augment_catalog_with_manifest(
    catalog: dict[str, Any], manifest: dict[str, Any] | None, *, precomputed_only: bool
) -> dict[str, Any]:
    publication = {
        "enabled": manifest is not None,
        "precomputed_only": precomputed_only,
        "status": manifest.get("status", "publishing") if manifest else "direct",
        "updated_at": manifest.get("updated_at") if manifest else None,
        "calculation_scope": manifest.get("calculation_scope") if manifest else None,
        "progress": dict(manifest.get("progress") or {}) if manifest else None,
    }
    catalog["publication"] = publication
    if not manifest:
        return catalog
    manifest_run = manifest.get("run")
    # El visor no debe ofrecer horas que no se van a calcular: de los productos
    # recortados solo existen los primeros plazos.
    diagnostic_hours = int((manifest.get("expected_hours") or {}).get("diagnostic") or 0)
    if diagnostic_hours > 0:
        for product in CAPPED_FORECAST_PRODUCTS:
            product_catalog = catalog.get("products", {}).get(product)
            if not product_catalog or product_catalog.get("run") != manifest_run:
                continue
            times = sorted(set(product_catalog.get("valid_times", ())))
            product_catalog["valid_times"] = times[:diagnostic_hours]

    for product, state in manifest.get("products", {}).items():
        product_catalog = catalog.get("products", {}).get(product)
        if not product_catalog or product_catalog.get("run") != manifest_run:
            continue
        expected = set(product_catalog.get("valid_times", ()))
        available = sorted(set(state.get("available_times", ())) & expected)
        product_catalog["available_times"] = available
        product_catalog["publishing"] = len(available) < len(product_catalog.get("valid_times", ()))
        product_catalog["available_until"] = available[-1] if available else None
        # Horas que el mapa va a tener cuando la pasada termine. Las que
        # anuncia el catálogo van creciendo mientras Météo-France publica, así
        # que sirven para saber qué se puede pedir, pero no como denominador
        # de un porcentaje: daría 100 % a media pasada.
        total_final = (manifest.get("expected_totals") or {}).get(product)
        if total_final:
            product_catalog["expected_total"] = int(total_final)
    return catalog
