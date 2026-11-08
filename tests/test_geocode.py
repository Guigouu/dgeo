# SPDX-License-Identifier: AGPL-3.0-or-later
"""Address / coordinates input (no network: Nominatim is mocked)."""
import pytest

from dgeo.geocode import geocode, parse_coords


@pytest.mark.parametrize("text, expected", [
    ("40.4168, -3.7038", (40.4168, -3.7038)),
    ("40.4168 -3.7038", (40.4168, -3.7038)),
    (" -33.86 ; 151.21 ", (-33.86, 151.21)),
    ("40,4168; -3,7038", (40.4168, -3.7038)),
    ("40,4168 -3,7038", (40.4168, -3.7038)),
])
def test_parse_coords(text, expected):
    assert parse_coords(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["Puerta del Sol, Madrid", "95, 10", "40.4", "", "12 rue de la Paix"])
def test_not_coords(text):
    assert parse_coords(text) is None


def test_coords_do_not_hit_the_network():
    class Boom:
        def get(self, *a, **k):
            raise AssertionError("network used")
    assert geocode("1.5, 2.5", session=Boom())[0].lat == 1.5


def test_nominatim_query():
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"display_name": "Somewhere", "lat": "10.5", "lon": "-20.25"}]

    class Sess:
        def get(self, url, params, headers, timeout):
            seen.update(url=url, params=params, headers=headers)
            return Resp()

    res = geocode("some place", session=Sess())
    assert (res[0].name, res[0].lat, res[0].lon) == ("Somewhere", 10.5, -20.25)
    assert seen["params"]["q"] == "some place" and seen["headers"]["User-Agent"].startswith("dgeo/")
