import numpy as np

from server.services.arome_forecast import _precipitation_type_classes


def test_precipitation_type_preserves_classes_and_groups_variants():
    source = np.array([
        0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12,
        193, 201, 205, 206, 207, 213,
        2, 4, 9999, np.nan,
    ])
    result = _precipitation_type_classes(source)

    np.testing.assert_array_equal(
        result[:17],
        [0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12, 6, 1, 5, 6, 7, 6],
    )
    assert np.isnan(result[17:]).all()
