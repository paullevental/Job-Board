"""Adapter parsing tests, against recorded payloads from each vendor."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
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


class TestDetailParsing:
    """Each vendor returns its description differently. These shapes were captured
    from live responses on 2026-10-13, not assumed."""

    def test_greenhouse_unescapes_its_content(self):
        """Greenhouse double-encodes: `content` arrives as `&lt;p&gt;`, so without
        unescaping the user would read literal tags instead of a description."""
        html = GreenhouseAdapter().parse_detail(load("greenhouse_detail.json"), "8805655002")

        assert "<p>" in html
        assert "&lt;p&gt;" not in html
        assert "Our Commitment to Diversity and Inclusion" in html

    def test_greenhouse_reports_a_missing_body(self):
        assert GreenhouseAdapter().parse_detail({"id": 1}, "1") == ""

    def test_lever_keeps_its_raw_html(self):
        html = LeverAdapter().parse_detail(load("lever_detail.json"), "any-id")

        assert "A World-Changing Company" in html
        assert "&lt;div&gt;" not in html

    def test_lever_includes_the_list_sections(self):
        """Lever splits a posting across `description`, `lists[]` and `additional`.
        Taking only `description` silently drops the requirements."""
        html = LeverAdapter().parse_detail(load("lever_detail.json"), "any-id")

        assert "What We Value" in html

    def test_lever_includes_the_closing_section(self):
        html = LeverAdapter().parse_detail(load("lever_detail.json"), "any-id")

        assert "Life at Palantir" in html

    def test_ashby_finds_the_posting_inside_the_board(self):
        """Ashby publishes no per-posting endpoint, so the whole board comes back
        and the posting has to be located by id."""
        payload = load("ashby_detail.json")
        wanted = payload["jobs"][0]["id"]

        html = AshbyAdapter().parse_detail(payload, wanted)

        assert "About Ramp" in html

    def test_ashby_returns_nothing_for_a_posting_not_on_the_board(self):
        """A posting that closed is simply absent from the board array."""
        assert AshbyAdapter().parse_detail(load("ashby_detail.json"), "gone-123") == ""

    def test_ashby_separates_a_missing_posting_from_an_empty_one(self):
        """Both read as "no description", but only one means the role closed, and
        reporting an empty posting as gone would be a lie the data can disprove."""
        payload = load("ashby_detail.json")
        payload["jobs"][0]["descriptionHtml"] = ""
        found_but_empty = payload["jobs"][0]["id"]
        adapter = AshbyAdapter()

        assert adapter.find_job(payload, found_but_empty) is not None
        assert adapter.find_job(payload, "not-on-this-board") is None


class TestDetailFetching:
    """The HTTP half of the detail path. A dead board must degrade to a
    `DetailResult`, never an exception -- the same contract board fetches have."""

    @staticmethod
    def call(adapter, ext_id, *, status=200, payload=None, body=""):
        def handler(_request):
            if payload is not None:
                return httpx.Response(status, json=payload)
            return httpx.Response(status, text=body)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await adapter.fetch_detail(client, "acme", ext_id)

        return asyncio.run(run())

    def test_greenhouse_returns_the_description(self):
        result = self.call(
            GreenhouseAdapter(), "8805655002", payload=load("greenhouse_detail.json")
        )

        assert result.ok
        assert "<p>" in result.html

    def test_greenhouse_reports_a_missing_posting(self):
        result = self.call(GreenhouseAdapter(), "nope", status=404, payload={"error": "x"})

        assert not result.ok
        assert "404" in result.error

    def test_greenhouse_survives_a_server_error(self):
        result = self.call(GreenhouseAdapter(), "1", status=500, body="boom")

        assert not result.ok
        assert result.error

    def test_greenhouse_survives_malformed_json(self):
        result = self.call(GreenhouseAdapter(), "1", body="not json at all")

        assert not result.ok

    def test_lever_reports_a_missing_posting(self):
        result = self.call(LeverAdapter(), "nope", status=404, payload={"ok": False})

        assert not result.ok
        assert "404" in result.error

    def test_lever_returns_the_composed_description(self):
        payload = load("lever_detail.json")

        result = self.call(LeverAdapter(), payload["id"], payload=payload)

        assert result.ok
        assert "What We Value" in result.html

    def test_ashby_reports_a_posting_that_left_the_board(self):
        result = self.call(AshbyAdapter(), "gone-123", payload=load("ashby_detail.json"))

        assert not result.ok
        assert "no longer on the board" in result.error

    def test_ashby_returns_an_empty_body_as_a_success(self):
        """Present but blank is not the same as gone; `details.py` decides what an
        empty description means, so the adapter must not pre-judge it."""
        payload = load("ashby_detail.json")
        payload["jobs"][0]["descriptionHtml"] = ""

        result = self.call(AshbyAdapter(), payload["jobs"][0]["id"], payload=payload)

        assert result.ok
        assert result.html == ""


def test_a_fetch_error_does_not_leak_the_board_url():
    """httpx puts the full request URL in HTTPStatusError, and that URL carries
    the board token. The client gets the failure, not our request details."""
    def handler(_request):
        return httpx.Response(500, text="boom")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GreenhouseAdapter().fetch_detail(client, "secret-token", "1")

    result = asyncio.run(run())

    assert not result.ok
    assert "secret-token" not in result.error
    assert "http" not in result.error.lower()


class TestDetailIdentity:
    """`follow_redirects=True` means a board can answer with a different posting
    than the one we asked for. Serving that description under our posting's id
    would be quietly wrong, so the id is verified."""

    def call(self, adapter, ext_id, payload):
        def handler(_request):
            return httpx.Response(200, json=payload)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await adapter.fetch_detail(client, "acme", ext_id)

        return asyncio.run(run())

    def test_greenhouse_rejects_a_payload_for_another_posting(self):
        result = self.call(GreenhouseAdapter(), "11111", load("greenhouse_detail.json"))

        assert not result.ok
        assert "different posting" in result.error

    def test_lever_rejects_a_payload_for_another_posting(self):
        result = self.call(LeverAdapter(), "11111", load("lever_detail.json"))

        assert not result.ok
        assert "different posting" in result.error
