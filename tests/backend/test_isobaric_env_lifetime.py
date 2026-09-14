from datetime import datetime, timezone
import numpy as np
import rasterio
from rasterio.transform import from_origin
from server.services.arome_packages import _index_isobaric_bands


def test_packages_can_close_in_opening_order(tmp_path):
    run = datetime(2026, 9, 13, tzinfo=timezone.utc)
    path = tmp_path / 'profile.tif'
    with rasterio.open(path, 'w', driver='GTiff', height=2, width=2,
                       count=1, dtype='float32', transform=from_origin(1, 2, 1, 1)) as ds:
        ds.write(np.ones((2, 2), dtype='float32'), 1)
        ds.update_tags(1, GRIB_ELEMENT='TMP', GRIB_VALID_TIME=str(int(run.timestamp())),
                       GRIB_SHORT_NAME='85000-ISBL')
    first = _index_isobaric_bands(path, run, [850], {'TMP': 'temperature'})
    second = _index_isobaric_bands(path, run, [850], {'TMP': 'temperature'})
    try:
        first.close()
        np.testing.assert_array_equal(second.decode(1), np.ones((2, 2)))
    finally:
        second.close()
    assert not rasterio.env.hasenv()
