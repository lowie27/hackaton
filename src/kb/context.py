"""Context-aware re-ranking that explains itself.

Search relevance (BM25/vector) says a chunk is *about* the query. This module
adds whether it *applies* to the person asking and whether it can be *trusted*:
same country and department, still valid, recently updated, official source,
accountable owner. Every adjustment comes with a sentence, so the UI can show
why a result moved up or down instead of hiding it in a black box.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime

# Relative adjustments; the final score is base * (1 + sum), floored at MIN_FACTOR.
COUNTRY_MATCH = 0.3
COUNTRY_OTHER = -0.5
DEPARTMENT_MATCH = 0.2
DEPARTMENT_OTHER = -0.2
LOCATION_MATCH = 0.1
FRESH = 0.2  # updated within FRESH_DAYS
STALE = -0.3  # not updated for STALE_DAYS
EXPIRED = -0.6
NOT_YET_VALID = -0.3
NO_OWNER = -0.1
MANAGER_UPLOAD = 0.1
SOURCE_WEIGHT = {"policy": 0.2, "teams": -0.1, "email": -0.1, "chat": -0.1}
FRESH_DAYS = 365
STALE_DAYS = 3 * 365
MIN_FACTOR = 0.1

# Signal names, so callers (the UI) can switch individual signals off.
SIGNALS = ("country", "department", "location", "validity", "freshness", "source", "owner", "uploader", "conflicts")
# Applied by the retriever after kb.conflicts compares the results with each other.
CONTRADICTED = -0.4


@dataclass
class UserContext:
    country: str | None = None
    location: str | None = None
    department: str | None = None
    position: str | None = None


@dataclass
class Assessment:
    adjustment: float = 0.0
    reasons: list[str] = field(default_factory=list)  # why it applies / can be trusted
    warnings: list[str] = field(default_factory=list)  # why to be careful

    @property
    def factor(self) -> float:
        return max(MIN_FACTOR, 1.0 + self.adjustment)

    def up(self, weight: float, reason: str) -> None:
        self.adjustment += weight
        self.reasons.append(reason)

    def down(self, weight: float, warning: str) -> None:
        self.adjustment += weight
        self.warnings.append(warning)


def _day(value: date | datetime | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


def _age(days: int) -> str:
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def assess(doc: dict, user: UserContext, today: date, signals: Collection[str] = SIGNALS) -> Assessment:
    """Score one document's metadata against the user's context.

    doc needs the keys country, location, department, source, owner,
    updated_at, valid_from, valid_until, uploader_position, uploader_is_manager
    (missing keys count as unknown). Signals not in `signals` are skipped.
    """
    a = Assessment()
    on = set(signals)

    country = doc.get("country")
    if "country" not in on:
        pass
    elif country and user.country:
        if _same(country, user.country):
            a.up(COUNTRY_MATCH, f"applies to your country ({country})")
        else:
            a.down(COUNTRY_OTHER, f"applies to {country}, you work in {user.country}")
    elif not country:
        a.reasons.append("applies to all countries")

    department = doc.get("department") if "department" in on else None
    if department and user.department:
        if _same(department, user.department):
            a.up(DEPARTMENT_MATCH, f"written for your department ({department})")
        else:
            a.down(DEPARTMENT_OTHER, f"written for {department}, not {user.department}")

    if "location" in on and _same(doc.get("location"), user.location):
        a.up(LOCATION_MATCH, f"specific to your site ({doc['location']})")

    valid_from, valid_until = _day(doc.get("valid_from")), _day(doc.get("valid_until"))
    if "validity" not in on:
        pass
    elif valid_until and valid_until < today:
        a.down(EXPIRED, f"expired on {valid_until.isoformat()}")
    elif valid_from and valid_from > today:
        a.down(NOT_YET_VALID, f"only valid from {valid_from.isoformat()}")
    elif valid_from or valid_until:
        a.reasons.append("currently in force")

    updated = _day(doc.get("updated_at"))
    if "freshness" not in on:
        pass
    elif updated is None:
        a.warnings.append("no update date")
    else:
        days = (today - updated).days
        if days <= FRESH_DAYS:
            a.up(FRESH, f"updated {_age(max(days, 0))}")
        elif days >= STALE_DAYS:
            a.down(STALE, f"last updated {_age(days)}")

    source = (doc.get("source") or "").lower()
    weight = SOURCE_WEIGHT.get(source, 0.0) if "source" in on else 0.0
    if weight > 0:
        a.up(weight, f"official {source} document")
    elif weight < 0:
        a.down(weight, f"informal source ({source})")

    if "owner" not in on:
        pass
    elif doc.get("owner"):
        a.reasons.append(f"owned by {doc['owner']}")
    else:
        a.down(NO_OWNER, "no accountable owner")

    if "uploader" not in on:
        pass
    elif doc.get("uploader_is_manager"):
        who = doc.get("uploader_position") or "a group manager"
        a.up(MANAGER_UPLOAD, f"uploaded by a manager of its group ({who})")
    elif doc.get("uploader_position"):
        a.reasons.append(f"uploaded by a {doc['uploader_position']}")

    return a
