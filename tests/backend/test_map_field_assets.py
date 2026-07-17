import io
import json
from datetime import datetime, timezone

from PIL import Image

from server.services import map_field_assets
from tabs import map as map_tab


def _tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 4), (20, 40, 60, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_ranking_build_publishes_and_reuses_split_map_assets(tmp_path, monkeypatch):
    manifest_path = tmp_path / "map_field_assets.json"
    monkeypatch.setattr(map_field_assets, "STATIC_DIR", tmp_path)
    monkeypatch.setattr(map_field_assets, "MANIFEST_PATH", manifest_path)

    calls = []

    def renderer(points):
        calls.append(list(points))
        return _tiny_png()

    specs = tuple(
        {
            "mode": mode,
            "prefix": f"{mode}_field",
            "identity_prefix": f"{mode}-field",
            "algorithm": 1,
            "palette": 2,
            "points": lambda: [(40.0, -3.0, 18.0)],
            "renderer": renderer,
        }
        for mode in ("temperature", "wind", "precipitation")
    )
    monkeypatch.setattr(map_field_assets, "_mode_specs", lambda store: specs)

    class Store:
        updated_at = datetime(2026, 7, 16, 20, 30, tzinfo=timezone.utc)

    first = map_field_assets.build_map_field_assets(Store())
    assert len(calls) == 3
    assert first["updated_at"] == Store.updated_at.isoformat()
    assert set(first["fields"]) == {"temperature", "wind", "precipitation"}
    for asset in first["fields"].values():
        assert len(asset["tiles"]) == 2
        assert all((tmp_path / tile["file"]).is_file() for tile in asset["tiles"])

    second = map_field_assets.build_map_field_assets(Store())
    assert len(calls) == 3
    assert second == json.loads(manifest_path.read_text(encoding="utf-8"))


def test_streamlit_uses_prebuilt_tiles_for_matching_snapshot(tmp_path, monkeypatch):
    version = "2026-07-16T20:30:00+00:00"
    filenames = ("temperature_field_test_0.png", "temperature_field_test_1.png")
    for filename in filenames:
        (tmp_path / filename).write_bytes(b"png")
    manifest = {
        "manifest_version": 1,
        "fields": {
            "temperature": {
                "version": version,
                "algorithm": 5,
                "palette": 2,
                "tiles": [
                    {"file": filenames[0], "bounds": [-180, -60, 0, 85]},
                    {"file": filenames[1], "bounds": [0, -60, 180, 85]},
                ],
            },
        },
    }
    manifest_path = tmp_path / "map_field_assets.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(map_tab, "_MAP_FIELD_STATIC_DIR", tmp_path)
    monkeypatch.setattr(map_tab, "_MAP_FIELD_MANIFEST_PATH", manifest_path)

    tiles = map_tab._prebuilt_field_tiles("temperature", version, 2, 5)
    assert tiles == (
        (filenames[0], (-180.0, -60.0, 0.0, 85.0)),
        (filenames[1], (0.0, -60.0, 180.0, 85.0)),
    )
    assert map_tab._prebuilt_field_tiles("temperature", "other", 2, 5) == ()
    assert map_tab._prebuilt_field_tiles(
        "temperature", "other", 2, 5, allow_previous_version=True,
    ) == tiles
