"""Decide whether a posting is an internship, co-op or new-grad role, and
whether a Canadian student could plausibly apply to it.

Recall is favoured over precision: a missed posting costs an opportunity, a
false positive costs three seconds of reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    INTERN = "intern"      # internship or co-op term
    NEW_GRAD = "new_grad"  # entry-level role aimed at graduating students
    OTHER = "other"        # everything else; filtered out by default


# Word-boundary anchored so "international" does not match "intern".
_INTERN_PATTERNS = [
    r"\bintern\b",
    r"\binterns\b",
    r"\binternship[s]?\b",
    r"\bco[\s\-/]?op\b",
    r"\bcoop\b",
    r"\bindustrial placement\b",
    r"\bplacement student\b",
    r"\bstudent (?:developer|engineer|programmer|analyst|researcher|position|role|opportunit)",
    r"\bsummer (?:analyst|associate|scholar)\b",
    r"\bwork term\b",
    r"\bapprentice(?:ship)?\b",
    r"\bstage\b",            # French-Canadian postings: "Stage en génie logiciel"
    r"\bstagiaire\b",
]

_NEW_GRAD_PATTERNS = [
    r"\bnew[\s\-]?grad(?:uate)?\b",
    r"\buniversity grad(?:uate)?\b",
    r"\bcollege grad(?:uate)?\b",
    r"\bcampus hire\b",
    r"\bearly career\b",
    r"\bentry[\s\-]level\b",
    r"\bgraduate (?:software|engineer|developer|program|scheme|analyst)",
    r"\b(?:software engineer|developer|engineer)[,\s\-]+(?:new\s*grad|university\s*grad)",
    r"\b(?:swe|sde)\s*[iI1]\b",
    # Roman-numeral I is an entry-level rung; the seniority veto catches II and up.
    r"\b(?:software engineer|software developer|engineer|developer)\s+[I1]\b",
]

# Disqualifying even if a match pattern fired: "Senior Manager, University
# Recruiting" is a real job, but not one a student can take.
_SENIORITY_PATTERNS = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b", r"\bdistinguished\b",
    r"\blead\b", r"\bhead of\b", r"\bmanager\b", r"\bdirector\b", r"\bvp\b",
    r"\bvice president\b", r"\bexecutive\b", r"\barchitect\b", r"\bfellow\b",
    r"\bii+\b", r"\b[3-9]\b",
]

_NON_ENG_PATTERNS = [
    r"\brecruit(?:er|ing|ment)\b",
    r"\bprogram manager\b",
    r"\btalent\b",
    r"\bsales\b", r"\bmarketing\b", r"\baccount executive\b",
    r"\bcustomer success\b", r"\bsupport specialist\b",
]

# Used only to rank. Absence does not exclude a posting.
_ENG_PATTERNS = [
    r"\bsoftware\b", r"\bengineer(?:ing)?\b", r"\bdeveloper\b", r"\bdevelopment\b",
    r"\bprogramm(?:er|ing)\b", r"\bcomputer science\b", r"\bbackend\b", r"\bfrontend\b",
    r"\bfull[\s\-]?stack\b", r"\bdata\b", r"\bmachine learning\b", r"\bml\b", r"\bai\b",
    r"\bplatform\b", r"\binfrastructure\b", r"\bsecurity\b", r"\bmobile\b", r"\bios\b",
    r"\bandroid\b", r"\bweb\b", r"\bqa\b", r"\bsre\b", r"\bdevops\b", r"\bcloud\b",
    r"\bsystems\b", r"\bresearch\b", r"\bquant\b", r"\bgénie logiciel\b", r"\blogiciel\b",
]

_INTERN_RE = re.compile("|".join(_INTERN_PATTERNS), re.IGNORECASE)
_NEW_GRAD_RE = re.compile("|".join(_NEW_GRAD_PATTERNS), re.IGNORECASE)
_SENIORITY_RE = re.compile("|".join(_SENIORITY_PATTERNS), re.IGNORECASE)
_NON_ENG_RE = re.compile("|".join(_NON_ENG_PATTERNS), re.IGNORECASE)
_ENG_RE = re.compile("|".join(_ENG_PATTERNS), re.IGNORECASE)

_SEASON_RE = re.compile(
    r"\b(summer|fall|autumn|winter|spring|may|september|january)\b[\s\-/]*((?:19|20)\d{2})?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20[2-9]\d)\b")

_SEASON_CANON = {
    "summer": "Summer", "may": "Summer",
    "fall": "Fall", "autumn": "Fall", "september": "Fall",
    "winter": "Winter", "january": "Winter",
    "spring": "Spring",
}

_CANADA_PATTERNS = [
    r"\bcanada\b", r"\bcanadian\b",
    r"\btoronto\b", r"\bvancouver\b", r"\bmontr[eé]al\b", r"\bottawa\b",
    r"\bwaterloo\b", r"\bkitchener\b", r"\bcalgary\b", r"\bedmonton\b",
    r"\bwinnipeg\b", r"\bhalifax\b", r"\bqu[eé]bec\b", r"\bvictoria\b",
    r"\bburnaby\b", r"\brichmond hill\b", r"\bmississauga\b", r"\bbrampton\b",
    r"\bhamilton,?\s*on\b", r"\blondon,?\s*on\b", r"\bguelph\b", r"\bsaskatoon\b",
    r"\bregina\b", r"\bst\.?\s*john'?s\b", r"\bfredericton\b", r"\bmoncton\b",
    r"\bkelowna\b", r"\bkanata\b", r"\bmarkham\b", r"\bvaughan\b",
    r"\bon,?\s*(?:ca|canada)\b", r"\bbc,?\s*(?:ca|canada)\b",
    r"\bab,?\s*(?:ca|canada)\b", r"\bqc,?\s*(?:ca|canada)\b",
    r"\bontario\b", r"\bbritish columbia\b", r"\balberta\b", r"\bmanitoba\b",
    r"\bnova scotia\b", r"\bnew brunswick\b", r"\bsaskatchewan\b",
    r"\bnewfoundland\b", r"\bprince edward island\b",
]
_CANADA_RE = re.compile("|".join(_CANADA_PATTERNS), re.IGNORECASE)

_REMOTE_RE = re.compile(r"\bremote\b|\bdistributed\b|\bwork from home\b|\banywhere\b", re.IGNORECASE)
_NA_REMOTE_RE = re.compile(r"north america|\bnamer\b|\bus\s*/\s*canada\b|\bamericas\b", re.IGNORECASE)

# A bare "Remote" next to one of these should not read as Canada-friendly.
_FOREIGN_ONLY_RE = re.compile(
    r"\b(?:united kingdom|london, uk|dublin|berlin|munich|paris|amsterdam|zurich|"
    r"singapore|tokyo|sydney|melbourne|bangalore|bengaluru|hyderabad|mumbai|"
    r"tel aviv|s[aã]o paulo|mexico city|warsaw|krak[oó]w|lisbon|madrid|barcelona)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    """What we concluded about a posting, and why."""

    category: Category
    canada_eligible: bool
    is_remote: bool
    season: str | None          # "Summer", "Fall", "Winter", "Spring"
    year: int | None
    engineering: bool
    reasons: tuple[str, ...]

    @property
    def term(self) -> str | None:
        if self.season and self.year:
            return f"{self.season} {self.year}"
        if self.season:
            return self.season
        if self.year:
            return str(self.year)
        return None

    @property
    def interesting(self) -> bool:
        return self.category is not Category.OTHER


def _extract_term(text: str) -> tuple[str | None, int | None]:
    season: str | None = None
    year: int | None = None

    match = _SEASON_RE.search(text)
    if match:
        season = _SEASON_CANON.get(match.group(1).lower())
        if match.group(2):
            year = int(match.group(2))

    # A posting can name two terms at once ("Summer 2028 / 2027"). Take the
    # nearer year: that is the term being decided now.
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if years:
        year = min(years) if year is None else min(year, min(years))
    return season, year


def classify(title: str, location: str = "", department: str = "") -> Classification:
    """Classify a posting from its title and location."""
    title = title or ""
    location = location or ""
    haystack = f"{title} {department}"
    reasons: list[str] = []

    category = Category.OTHER
    if _INTERN_RE.search(haystack):
        category = Category.INTERN
        reasons.append("title matched internship/co-op pattern")
    elif _NEW_GRAD_RE.search(haystack):
        category = Category.NEW_GRAD
        reasons.append("title matched new-grad pattern")

    # An internship title plus a seniority marker usually means a role that
    # *manages* interns -- but "Software Engineer Intern II" is a real posting,
    # so only veto when the seniority word is not adjacent to an intern marker.
    if category is not Category.OTHER and _SENIORITY_RE.search(title):
        if not re.search(r"\b(?:intern|co[\s\-]?op)\b\s*(?:ii+|[2-9])\b", title, re.IGNORECASE):
            category = Category.OTHER
            reasons.append("vetoed: seniority marker in title")

    if category is not Category.OTHER and _NON_ENG_RE.search(title):
        category = Category.OTHER
        reasons.append("vetoed: non-engineering function")

    engineering = bool(_ENG_RE.search(haystack))
    if engineering:
        reasons.append("engineering signal present")

    season, year = _extract_term(title)
    if season or year:
        reasons.append(f"term parsed: {season or '?'} {year or '?'}")

    is_remote = bool(_REMOTE_RE.search(location))
    canada_eligible = False
    if _CANADA_RE.search(location):
        canada_eligible = True
        reasons.append("Canadian location")
    elif is_remote and _NA_REMOTE_RE.search(location):
        canada_eligible = True
        reasons.append("remote, North America scope")
    elif is_remote and not _FOREIGN_ONLY_RE.search(location):
        # Bare "Remote" with no country. Keep it: a miss is the costly error.
        canada_eligible = True
        reasons.append("remote, unspecified region (ambiguous)")
    elif not location:
        canada_eligible = True
        reasons.append("no location given (ambiguous)")

    return Classification(
        category=category,
        canada_eligible=canada_eligible,
        is_remote=is_remote,
        season=season,
        year=year,
        engineering=engineering,
        reasons=tuple(reasons),
    )
