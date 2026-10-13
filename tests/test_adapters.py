"""Adapter parsing tests, against recorded payloads from each vendor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_poller.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_poller.ats.base import parse_epoch_millis, parse_iso

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


class TestGreenhouse:
    def setup_method(self):
        self.adapter = GreenhouseAdapter()
        self.postings = self.adapter.parse("acme", load("greenhouse_sample.json"))

    def test_parses_all_jobs(self):
        assert len(self.postings) == 5

    def test_extracts_nested_location(self):
        p = next(p for p in self.postings if p.external_id == "5001")
        assert p.location == "Toronto, ON, Canada"
        assert p.department == "Engineering"

    def test_ids_are_strings(self):
        assert all(isinstance(p.external_id, str) for p in self.postings)

    def test_parses_timestamp(self):
        p = next(p for p in self.postings if p.external_id == "5001")
        assert p.posted_at is not None
        assert p.posted_at.year == 2026

    def test_skips_jobs_without_id(self):
        assert self.adapter.parse("acme", {"jobs": [{"title": "No ID"}]}) == []

    def test_handles_empty_payload(self):
        assert self.adapter.parse("acme", {}) == []


class TestLever:
    def setup_method(self):
        self.adapter = LeverAdapter()
        self.postings = self.adapter.parse("beta", load("lever_sample.json"))

    def test_parses_all_jobs(self):
        assert len(self.postings) == 3

    def test_title_comes_from_text_field(self):
        p = next(p for p in self.postings if p.external_id == "aaa-111")
        assert p.title == "Engineering Intern - Summer 2027"

    def test_epoch_millis_timestamps(self):
        """Lever uses epoch millis, not ISO. Regression guard."""
        p = next(p for p in self.postings if p.external_id == "aaa-111")
        assert p.posted_at is not None
        assert p.posted_at.year == 2025  # 1759320000000 -> Oct 2025

    def test_location_from_categories(self):
        p = next(p for p in self.postings if p.external_id == "ccc-333")
        assert p.location == "Montréal, QC"

    def test_empty_board_is_not_an_error(self):
        assert self.adapter.parse("beta", []) == []


class TestAshby:
    def setup_method(self):
        self.adapter = AshbyAdapter()
        self.postings = self.adapter.parse("gamma", load("ashby_sample.json"))

    def test_parses_all_jobs(self):
        assert len(self.postings) == 3

    def test_flat_location_string(self):
        p = next(p for p in self.postings if p.external_id == "ash-1")
        assert p.location == "London, UK"


class TestFingerprinting:
    def test_identical_postings_share_a_fingerprint(self):
        a = GreenhouseAdapter().parse("acme", load("greenhouse_sample.json"))
        b = GreenhouseAdapter().parse("acme", load("greenhouse_sample.json"))
        assert [p.fingerprint() for p in a] == [p.fingerprint() for p in b]

    def test_title_edit_changes_fingerprint(self):
        payload = load("greenhouse_sample.json")
        before = GreenhouseAdapter().parse("acme", payload)[0].fingerprint()
        payload["jobs"][0]["title"] = "Software Engineer Intern, Fall 2027"
        after = GreenhouseAdapter().parse("acme", payload)[0].fingerprint()
        assert before != after

    def test_dedupe_key_ignores_case_and_spacing(self):
        p1 = GreenhouseAdapter().parse("acme", {"jobs": [
            {"id": 1, "title": "Software  Engineer Intern", "location": {"name": "Toronto, ON"}}
        ]})[0]
        p2 = GreenhouseAdapter().parse("other", {"jobs": [
            {"id": 99, "title": "software engineer intern", "location": {"name": "toronto ON"}}
        ]})[0]
        assert p1.dedupe_key() == p2.dedupe_key()


class TestTimestampHelpers:
    @pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45"])
    def test_bad_iso_returns_none(self, value):
        assert parse_iso(value) is None

    def test_z_suffix_is_handled(self):
        assert parse_iso("2026-10-01T14:00:00Z") is not None

    @pytest.mark.parametrize("value", [None, "abc", {}])
    def test_bad_epoch_returns_none(self, value):
        assert parse_epoch_millis(value) is None
