"""Place-name normalisation shared by the data loader and the input geocoder."""

import re
import unicodedata

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

_ABBREVIATIONS = [
    (re.compile(r"\b(saint|ste|st)\b\.?"), "st"),
    (re.compile(r"\bmount\b"), "mt"),
    (re.compile(r"\bfort\b"), "ft"),
]

# Legal/statistical descriptors the Census appends to names ("Tulsa city", "Bensalem township").
_GAZ_GOVT = re.compile(
    r"\s+(city and borough|consolidated government|unified government|metropolitan government|"
    r"urban county)$",
    re.IGNORECASE,
)
_GAZ_SUFFIX = re.compile(
    r"\s+(charter township|township|city|town|village|borough|municipality|cdp|ccd|"
    r"plantation|gore|grant|location|purchase|district|precinct|unorganized territory|ut)$",
    re.IGNORECASE,
)


def normalise(name):
    """'Mc Calla' == 'McCalla', 'Saint Johns' == 'St. Johns', 'Fort Worth' == 'Ft Worth'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    for pattern, repl in _ABBREVIATIONS:
        s = pattern.sub(repl, s)
    return re.sub(r"[^a-z0-9]", "", s)


def gazetteer_aliases(raw_name):
    """Candidate display names for a Census gazetteer entry, most specific first.

    'Athens-Clarke County unified government (balance)' -> ['Athens-Clarke County', 'Athens', ...]
    'Boise City city' -> ['Boise City', 'Boise']
    """
    name = re.sub(r"\s*\(.*?\)\s*", " ", raw_name).strip()
    name = _GAZ_GOVT.sub("", name).strip()
    name = _GAZ_SUFFIX.sub("", name, count=1).strip()  # only one: "Baker City city" -> "Baker City"
    aliases = [name]
    if "-" in name:
        aliases.extend(part.strip() for part in name.split("-") if part.strip())
    if name.lower().endswith(" city") and len(name) > 5:
        aliases.append(name[:-5])
    return aliases


def split_city_state(text):
    """Parse 'Austin, TX' / 'Austin TX' / 'Austin, Texas' -> ('Austin', 'TX') or None."""
    text = re.sub(r"\s+", " ", text.strip().rstrip("."))
    text = re.sub(r",?\s*(usa|us|united states( of america)?)$", "", text, flags=re.IGNORECASE)
    if "," in text:
        city, _, state = text.rpartition(",")
        city, state = city.strip(), state.strip()
    else:
        # No comma: try the trailing 2-letter code, then trailing full state names.
        parts = text.split(" ")
        city, state = None, None
        for n in (1, 2, 3):
            if len(parts) > n:
                cand = " ".join(parts[-n:])
                if cand.upper() in US_STATES or cand.lower() in STATE_BY_NAME:
                    city, state = " ".join(parts[:-n]), cand
                    break
        if city is None:
            return None
    code = state.upper() if state.upper() in US_STATES else STATE_BY_NAME.get(state.lower())
    if not code or not city:
        return None
    return city, code
