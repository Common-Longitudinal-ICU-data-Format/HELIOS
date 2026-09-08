from datetime import datetime, timezone

import pytest

from helios.fhir.clock import clock_predicate, parse_as_of


def test_parses_iso8601():
    assert parse_as_of("2110-03-15T08:00:00+00:00") == datetime(
        2110, 3, 15, 8, 0, tzinfo=timezone.utc)


def test_parses_z_suffix():
    assert parse_as_of("2110-03-15T08:00:00Z") == datetime(
        2110, 3, 15, 8, 0, tzinfo=timezone.utc)


def test_none_means_no_filter():
    assert parse_as_of(None) is None


def test_malformed_raises():
    with pytest.raises(ValueError):
        parse_as_of("yesterday")


def test_predicate_uses_the_tables_own_clock_column():
    t = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)
    assert "lab_result_dttm <=" in clock_predicate("labs", t)
    assert "recorded_dttm <=" in clock_predicate("vitals", t)
    assert "admin_dttm <=" in clock_predicate("medication_admin_continuous", t)


def test_no_as_of_means_empty_predicate():
    assert clock_predicate("labs", None) == ""


def test_table_without_a_clock_column_cannot_filter():
    """hospital_diagnosis has no timestamp - a documented look-ahead hole."""
    t = datetime(2110, 3, 15, 8, 0, tzinfo=timezone.utc)
    assert clock_predicate("hospital_diagnosis", t) == ""
