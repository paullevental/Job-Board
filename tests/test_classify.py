"""Classifier tests.

These double as the spec. When a real posting is misclassified, add it here
first, then fix the pattern.
"""

from __future__ import annotations

import pytest

from job_poller.classify import Category, classify


class TestInternDetection:
    @pytest.mark.parametrize("title", [
        "Software Engineer Intern",
        "Software Engineering Internship - Summer 2027",
        "Co-op Software Developer",
        "Coop Student, Backend",
        "Engineering Co-Op (Winter 2027)",
        "Summer Analyst, Technology",
        "Backend Developer Work Term",
        "Stagiaire en génie logiciel",
        "Student Developer, Platform",
        "Industrial Placement - Software",
    ])
    def test_matches_intern(self, title):
        assert classify(title).category is Category.INTERN

    @pytest.mark.parametrize("title", [
        "International Account Manager",   # "intern" inside "international"
        "Internal Tools Engineer",         # "intern" inside "internal"
    ])
    def test_word_boundary_prevents_false_positives(self, title):
        assert classify(title).category is not Category.INTERN


class TestNewGradDetection:
    @pytest.mark.parametrize("title", [
        "Software Engineer, New Grad",
        "New Graduate Software Developer",
        "Early Career Engineer",
        "Entry-Level Backend Developer",
        "Graduate Software Engineer",
        "University Grad - Software Engineering",
        "Software Engineer I",
    ])
    def test_matches_new_grad(self, title):
        assert classify(title).category is Category.NEW_GRAD


class TestSeniorityVeto:
    @pytest.mark.parametrize("title", [
        "Senior Manager, University Recruiting",
        "Director of Early Career Programs",
        "Staff Engineer, Intern Platform",
        "Head of Campus Hiring",
        "Principal Engineer",
        "Lead Recruiter, Internship Program",
    ])
    def test_rejects_senior_roles(self, title):
        assert classify(title).category is Category.OTHER

    def test_allows_numbered_intern_roles(self):
        """'Software Engineer Intern II' is a real intern posting."""
        assert classify("Software Engineer Intern II").category is Category.INTERN


class TestNonEngineeringVeto:
    @pytest.mark.parametrize("title", [
        "Marketing Intern",
        "Sales Intern, Enterprise",
        "Recruiting Coordinator Intern",
        "Customer Success Intern",
    ])
    def test_rejects_non_engineering(self, title):
        assert classify(title).category is Category.OTHER


class TestTermExtraction:
    @pytest.mark.parametrize("title,season,year", [
        ("Software Engineer Intern, Summer 2027", "Summer", 2027),
        ("Co-op Developer (Winter 2027)", "Winter", 2027),
        ("Fall 2026 Engineering Intern", "Fall", 2026),
        ("Spring 2027 Software Intern", "Spring", 2027),
        ("May 2027 Co-op", "Summer", 2027),
    ])
    def test_parses_term(self, title, season, year):
        c = classify(title)
        assert c.season == season
        assert c.year == year

    def test_year_without_season(self):
        c = classify("Software Engineer Intern 2027")
        assert c.year == 2027

    def test_no_term_is_none(self):
        assert classify("Software Engineer Intern").term is None

    def test_multiple_years_takes_nearest(self):
        assert classify("Intern Summer 2028 / 2027").year == 2027


class TestCanadaEligibility:
    @pytest.mark.parametrize("location", [
        "Toronto, ON, Canada",
        "Vancouver, BC",
        "Montréal, QC",
        "Waterloo, ON",
        "Ottawa, Ontario",
        "Calgary, AB, Canada",
        "British Columbia",
        "Halifax, Nova Scotia",
    ])
    def test_canadian_locations(self, location):
        assert classify("Software Engineer Intern", location).canada_eligible

    @pytest.mark.parametrize("location", [
        "London, UK",
        "Dublin, Ireland",
        "Bengaluru, India",
        "Singapore",
        "Tel Aviv",
    ])
    def test_foreign_locations_excluded(self, location):
        assert not classify("Software Engineer Intern", location).canada_eligible

    def test_remote_north_america_included(self):
        assert classify("Intern", "Remote - North America").canada_eligible

    def test_bare_remote_included_as_ambiguous(self):
        """A missed posting is worse than a false positive, so flag it."""
        c = classify("Intern", "Remote")
        assert c.canada_eligible
        assert any("ambiguous" in r for r in c.reasons)

    def test_remote_with_foreign_city_excluded(self):
        assert not classify("Intern", "Remote - London, UK").canada_eligible

    def test_us_only_location_excluded(self):
        assert not classify("Intern", "San Francisco, CA").canada_eligible


class TestEngineeringSignal:
    def test_detects_engineering(self):
        assert classify("Software Engineer Intern").engineering

    def test_absent_for_non_technical(self):
        assert not classify("Finance Intern", "Toronto").engineering

    def test_department_counts_as_signal(self):
        assert classify("Intern", "Toronto", department="Engineering").engineering


class TestReasons:
    def test_reasons_are_recorded(self):
        c = classify("Software Engineer Intern, Summer 2027", "Toronto, ON")
        assert c.reasons
        assert any("internship" in r for r in c.reasons)
        assert any("Canadian" in r for r in c.reasons)

    def test_veto_is_explained(self):
        """A title that matches and is then rejected should say why.

        (A title that never matched at all records no veto reason -- it was
        simply never a candidate. That is a different, correct path.)
        """
        c = classify("Senior Software Engineer Intern Program Owner", "Toronto")
        assert c.category is Category.OTHER
        assert any("vetoed" in r for r in c.reasons)


class TestEdgeCases:
    @pytest.mark.parametrize("title", ["", "   ", None])
    def test_empty_title_is_safe(self, title):
        assert classify(title).category is Category.OTHER

    def test_interesting_property(self):
        assert classify("Software Engineer Intern").interesting
        assert not classify("Senior Engineer").interesting
