"""Brazil operational admission does not determine training geography."""

import pytest

from app.services.territory import TerritoryUnavailable, classify_location


def test_inside_and_outside_are_not_confused_with_viewport():
    assert classify_location(covers=True, distance_m=0, accuracy_m=None) == "inside"
    assert classify_location(covers=False, distance_m=200_000, accuracy_m=20) == "outside"


def test_border_and_imprecise_points_are_reviewable_not_relocated():
    assert classify_location(covers=False, distance_m=200, accuracy_m=None) == "uncertain"
    assert classify_location(covers=False, distance_m=3000, accuracy_m=4000) == "uncertain"
    assert classify_location(covers=False, distance_m=6000, accuracy_m=100_000) == "outside"


def test_missing_territory_is_not_treated_as_inside():
    with pytest.raises(TerritoryUnavailable):
        classify_location(covers=None, distance_m=None, accuracy_m=None)
