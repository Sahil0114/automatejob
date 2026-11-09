"""Hard location/eligibility filtering, applied BEFORE scoring.

Classifies each job into exactly one of three buckets:

* ``confirmed``          — the user can realistically apply: preferred
                          on-site/hybrid cities (Mumbai metro), remote roles
                          explicitly open to India, or genuinely worldwide
                          ("Remote - Global", "Anywhere") remote roles.
* ``excluded``           — clearly out of reach: remote roles restricted to
                          a region/country that excludes India, or on-site
                          roles in a named foreign location.
* ``needs_verification`` — ambiguous: bare "Remote" with no region, regional
                          remote (APAC/Asia/EMEA), or locations we cannot
                          resolve. These are stored separately and are NEVER
                          labelled confirmed eligible.

All of it is driven by ``[eligibility]`` in config.toml, so location and
relocation preferences are user-configurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .config import AppConfig
from .models import Job

# Registry constants reused here (imported lazily to avoid a hard cycle with
# companies.py at module import time if needed).
from .companies import INDIA_REMOTE, WORLDWIDE_REMOTE

CONFIRMED = "confirmed"
EXCLUDED = "excluded"
NEEDS_VERIFICATION = "needs_verification"

ALL_STATUSES = (CONFIRMED, EXCLUDED, NEEDS_VERIFICATION)


# Remote region labels that mean the role is open to anywhere (including India).
# NOTE: "fully remote", "remote-first" etc. describe the WORK arrangement
# (no office), NOT geographic eligibility — a fully-remote company can still
# restrict to US-only. Only genuine geographic markers belong here.
_WORLDWIDE_REMOTE = [
    "global", "worldwide", "anywhere", "world", "global remote",
    "any timezone", "any time zone", "all timezones", "all time zones",
]

# Remote region labels that COULD include India but are not specific enough
# to confirm — these land in needs_verification.
_AMBIGUOUS_REMOTE = [
    "remote",  # bare "Remote" with no further region
    "apac", "asia", "asia pacific", "asia-pacific", "south asia", "southeast asia",
    "sea", "india wide", "within india",
]

# Remote region labels that exclude India — these are hard-excluded.
_RESTRICTED_REMOTE_REGIONS = [
    "amer", "americas", "north america", "south america", "latin america",
    "latam", "us", "usa", "united states", "canada", "emea", "europe",
    "middle east", "mena", "africa", "oceania", "australia", "new zealand",
]

# Named countries/states (word-boundary matched) that exclude India on a
# remote or on-site posting. This is a superset of what the legacy scorer used.
_RESTRICTED_WORDS = [
    "usa", "united states", "canada", "united kingdom", "uk", "ireland",
    "germany", "france", "netherlands", "spain", "italy", "portugal", "austria",
    "belgium", "switzerland", "sweden", "norway", "denmark", "finland", "poland",
    "czech republic", "romania", "estonia", "latvia", "lithuania", "greece",
    "israel", "uae", "united arab emirates", "saudi arabia", "qatar", "japan",
    "south korea", "china", "singapore", "hong kong", "australia", "new zealand",
    "brazil", "mexico", "argentina", "chile", "colombia", "philippines",
    "indonesia", "vietnam", "thailand", "malaysia", "taiwan", "turkey",
    # US states (city, state strings)
    "california", "texas", "florida", "washington", "massachusetts", "colorado",
    "illinois", "georgia", "virginia", "arizona", "oregon", "michigan",
    "pennsylvania", "utah", "nevada", "minnesota", "maryland", "wisconsin",
    "ohio", "tennessee", "missouri", "kentucky", "indiana", "alabama",
    "louisiana", "connecticut", "vermont", "maine", "idaho", "montana",
    "wyoming", "alaska", "hawaii", "iowa", "kansas", "oklahoma", "nebraska",
    "arkansas", "district of columbia",
]

# Uppercase US state/territory codes, matched on the ORIGINAL casing so
# "Boston, MA" is caught without matching ordinary words.
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

# India city signals (beyond the preferred metro).
_INDIA_CITIES = [
    "bengaluru", "bangalore", "delhi", "new delhi", "pune", "hyderabad",
    "gurgaon", "gurugram", "chennai", "noida", "kolkata", "ahmedabad",
    "jaipur", "kochi", "cochin", "chandigarh", "indore", "india",
]

# Foreign cities that exclude India when named on-site/hybrid (NOT country
# names, which are already in _RESTRICTED_WORDS). Single-word entries are
# word-boundary matched so "dublin" catches "Dublin, Ireland" without hitting
# unrelated words. Indian cities are deliberately absent.
_FOREIGN_CITY_PHRASES = [
    "san francisco", "los angeles", "palo alto", "mountain view", "salt lake city",
    "san jose", "san diego", "santa clara", "santa monica", "menlo park",
    "redwood city", "cupertino", "sunnyvale", "las vegas", "new orleans",
    "san antonio", "fort worth", "st louis", "ho chi minh", "tel aviv",
    "abu dhabi", "kuala lumpur", "sao paulo", "buenos aires", "rio de janeiro",
    "cape town", "johannesburg", "new haven", "the hague", "frankfurt am main",
]
_FOREIGN_CITY_WORDS = [
    "london", "dublin", "berlin", "munich", "hamburg", "frankfurt", "amsterdam",
    "paris", "madrid", "barcelona", "valencia", "lisbon", "porto", "milan",
    "rome", "turin", "warsaw", "krakow", "prague", "vienna", "brussels",
    "antwerp", "stockholm", "copenhagen", "oslo", "helsinki", "zurich", "geneva",
    "basel", "edinburgh", "manchester", "glasgow", "birmingham", "bristol",
    "leeds", "aarhus", "belgrade", "bucharest", "sofia", "budapest", "athens",
    "thessaloniki", "tallinn", "vilnius", "riga", "zagreb", "ljubljana",
    "bratislava", "toronto", "vancouver", "montreal", "ottawa", "calgary",
    "edmonton", "waterloo", "seattle", "austin", "boston", "denver", "chicago",
    "bellevue", "portland", "atlanta", "dallas", "houston", "phoenix",
    "honolulu", "miami", "minneapolis", "raleigh", "durham", "detroit",
    "pittsburgh", "philadelphia", "charlotte", "nashville", "tokyo", "seoul",
    "sydney", "melbourne", "brisbane", "perth", "adelaide", "taipei", "shanghai",
    "beijing", "shenzhen", "guangzhou", "jakarta", "manila", "bangkok",
    "auckland", "wellington", "dubai", "doha", "riyadh", "istanbul", "ankara",
    "cambridge", "oxford",
]


def _kw(kw: str, text: str) -> bool:
    """Substring match for short tokens, word-boundary for very short ones."""
    import re as _re
    if len(kw) <= 3:
        return bool(_re.search(r"\b" + _re.escape(kw) + r"\b", text))
    return kw in text


def _short_kw(kw: str, text: str) -> bool:
    import re as _re
    return bool(_re.search(r"\b" + _re.escape(kw) + r"\b", text))


@dataclass
class LocationAnalysis:
    """Structured read of a job's location fields."""
    is_remote: bool = False
    is_hybrid: bool = False
    preferred_city: Optional[str] = None   # name of a matched preferred city
    india_city: Optional[str] = None       # any other matched India city
    has_india: bool = False
    worldwide_remote: bool = False
    ambiguous_region: Optional[str] = None
    restricted_region: Optional[str] = None


def analyze_location(job: Job, config: AppConfig) -> LocationAnalysis:
    import re
    location_raw = job.location or ""
    location = location_raw.lower()
    workplace = (job.workplace_type or "").lower()
    # Secondary-location text (Ashby) can carry the real city when `location`
    # is just "Remote"; fold it in for analysis.
    secondary = " ".join(job.secondary_locations) if getattr(job, "secondary_locations", None) else ""
    combined = " ".join([location, workplace, secondary.lower()]).strip()
    text = f"{location} {workplace} {secondary.lower()}".strip()

    a = LocationAnalysis()
    a.is_remote = ("remote" in workplace) or ("remote" in location)
    a.is_hybrid = "hybrid" in workplace or "hybrid" in location

    # India / preferred-city signals.
    for city in config.eligibility.cities:
        if _kw(city, text):
            a.preferred_city = city
            a.has_india = True
            break
    for city in _INDIA_CITIES:
        if a.india_city:
            break
        # "india" must be word-boundary matched: it is a prefix of
        # "Indianapolis" / "Indiana" and must not fire on those.
        if city == "india":
            hit = bool(re.search(r"\bindia\b", text))
        else:
            hit = _kw(city, text)
        if hit:
            a.india_city = city
            a.has_india = True

    if a.is_remote:
        # Worldwide remote markers (checked before region restrictions).
        for marker in _WORLDWIDE_REMOTE:
            if _kw(marker, text):
                a.worldwide_remote = True
                break
        if a.worldwide_remote:
            return a
        # Remote explicitly tied to India.
        if a.has_india:
            return a
        # Remote tied to a region that excludes India.
        for region in _RESTRICTED_REMOTE_REGIONS:
            if _kw(region, text):
                a.restricted_region = region
                return a
        # A named foreign country/state on the remote posting.
        foreign = _foreign_country(text, location_raw)
        if foreign:
            a.restricted_region = foreign
            return a
        # A region that may or may not include India.
        for region in _AMBIGUOUS_REMOTE:
            if _kw(region, text):
                a.ambiguous_region = region
                return a
        # Bare "Remote" with nothing else recognisable.
        a.ambiguous_region = "remote (no region)"
        return a

    # Not remote: on-site / hybrid / unspecified.
    if a.preferred_city:
        return a
    if a.has_india:
        return a
    foreign = _foreign_country(text, location_raw)
    if foreign:
        a.restricted_region = foreign
        return a
    # No signal at all (e.g. empty location).
    a.ambiguous_region = "unresolved location"
    return a


def _foreign_country(text: str, raw: str) -> Optional[str]:
    import re as _re
    for phrase in ["united states", "united kingdom", "united arab emirates",
                   "south korea", "new zealand", "hong kong", "saudi arabia",
                   "czech republic", "new york", "new jersey", "north carolina",
                   "south carolina", "new hampshire", "west virginia",
                   "rhode island", "new mexico"] + _FOREIGN_CITY_PHRASES:
        if phrase in text:
            return phrase.title()
    for word in _RESTRICTED_WORDS + _FOREIGN_CITY_WORDS:
        if _short_kw(word, text):
            return word.title()
    if raw:
        for code in _re.findall(r"\b[A-Z]{2}\b", raw):
            if code in _US_STATE_CODES:
                return f"US ({code})"
    return None


@dataclass
class EligibilityResult:
    status: str            # confirmed | excluded | needs_verification
    reason: str
    analysis: LocationAnalysis
    preference: str = ""   # mumbai | relocation | remote ('' when excluded/needs_verification)


PREF_MUMBAI = "mumbai"
PREF_RELOCATION = "relocation"
PREF_REMOTE = "remote"


def classify_eligibility(job: Job, config: AppConfig, registry=None) -> EligibilityResult:
    """Split a job into employment-eligibility + personal location preference.

    ``status`` is employment eligibility (can I be employed here as an
    India-based candidate): confirmed / excluded / needs_verification.

    ``preference`` is the personal location preference, kept SEPARATE:
    mumbai (preferred metro) / relocation (other India) / remote
    (India-eligible or world-wide remote).

    An optional :class:`CompanyRegistry` upgrades a bare/ambiguous remote role
    to ``confirmed`` when the company is known to hire world-wide/India remote.
    """
    a = analyze_location(job, config)
    e = config.eligibility

    result = _classify_from_analysis(job, a, e)
    if registry is not None and result.status == NEEDS_VERIFICATION and a.is_remote:
        comp = registry.lookup(job.company)
        if comp and comp.remote_eligibility in (WORLDWIDE_REMOTE, INDIA_REMOTE):
            return EligibilityResult(
                CONFIRMED,
                f"remote role; company registry: {comp.remote_eligibility}",
                a,
                PREF_REMOTE,
            )
    return result


def _classify_from_analysis(job: Job, a: LocationAnalysis, e) -> EligibilityResult:
    if a.is_remote:
        if a.worldwide_remote:
            return EligibilityResult(CONFIRMED, "worldwide remote (India-eligible)", a, PREF_REMOTE)
        if a.has_india:
            return EligibilityResult(CONFIRMED, "remote role open to India", a, PREF_REMOTE)
        if a.restricted_region:
            return EligibilityResult(EXCLUDED,
                                     f"remote restricted to {a.restricted_region}", a, "")
        if a.ambiguous_region:
            return EligibilityResult(NEEDS_VERIFICATION,
                                     f"remote, unclear eligibility ({a.ambiguous_region})", a, "")

    # On-site / hybrid / unspecified.
    if a.preferred_city:
        return EligibilityResult(CONFIRMED,
                                 f"preferred city '{a.preferred_city}'", a, PREF_MUMBAI)
    if a.has_india:
        # Other Indian city -> relocation queue (not the default shortlist).
        if e.relocation:
            return EligibilityResult(CONFIRMED,
                                     f"India location '{a.india_city}' (relocation queue)", a,
                                     PREF_RELOCATION)
        return EligibilityResult(EXCLUDED,
                                 f"India location '{a.india_city}' outside preferred cities "
                                 "and relocation not accepted", a, "")
    if a.restricted_region:
        return EligibilityResult(EXCLUDED,
                                 f"location restricted to {a.restricted_region}", a, "")
    if e.relocation:
        return EligibilityResult(NEEDS_VERIFICATION,
                                 "unresolved location (relocation open — review)", a, "")
    return EligibilityResult(EXCLUDED, "location not in target and relocation not accepted", a, "")
