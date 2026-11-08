# SPDX-License-Identifier: AGPL-3.0-or-later
from dgeo.timeguess import guess_start, parse_creation

CT = parse_creation("2025-06-01T12:00:00.000000Z")
DUR = 3306.66


def test_end_hypothesis_when_start_would_end_in_future():
    # imported at 12:20 UTC; the "debut" (start) hypothesis would end at 12:55 UTC, i.e. after the import
    now = parse_creation("2025-06-01T12:20:00Z")
    g = guess_start(CT, DUR, now)
    assert g["choice"] == "fin"
    assert abs(g["hypotheses"]["fin"] - parse_creation("2025-06-01T11:04:53.34Z")) < 0.5


def test_adsb_overlap_decides():
    now = CT + 10 * 3600
    fr = (CT + 100, CT + 3000)  # ADS-B capture after creation_time => "debut" (start)
    assert guess_start(CT, DUR, now, fr)["choice"] == "debut"
    fr = (CT - 3000, CT - 100)
    assert guess_start(CT, DUR, now, fr)["choice"] == "fin"


def test_default_and_missing():
    assert guess_start(CT, DUR, CT + 10 * 3600)["choice"] == "fin"
    assert guess_start(None, DUR, CT)["choice"] is None
    assert parse_creation("2025-06-01 12:00:00") is None  # no time zone
