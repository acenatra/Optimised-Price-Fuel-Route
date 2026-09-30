"""Location autocomplete over the local Census gazetteer (no external calls, no Elasticsearch).

An indexed prefix query (`key LIKE 'abc%'`) on ~48k rows returns in well under 10 ms in SQLite,
and results are ranked by population so "chi" -> Chicago before Chinook. Typing "Austin, T"
narrows to states starting with "T"; "Austin TX" also works.
"""

import hashlib

from django.core.cache import cache

from ..models import Place
from .names import STATE_BY_NAME, US_STATES, normalise, split_city_state

MIN_CHARS = 2
LIMIT = 8


def _states_matching(fragment):
    frag = fragment.strip().lower()
    if not frag:
        return None
    codes = {c for c in US_STATES if c.lower().startswith(frag)}
    codes |= {code for name, code in STATE_BY_NAME.items() if name.startswith(frag)}
    return codes


def suggest(query, limit=LIMIT):
    query = (query or "").strip()
    if len(query) < MIN_CHARS:
        return []
    cache_key = "suggest:v2:" + hashlib.md5(f"{query.lower()}|{limit}".encode()).hexdigest()
    hit = cache.get(cache_key)
    if hit is not None:
        return hit

    def search(qs, prefix):
        return list(qs.filter(key__startswith=prefix).order_by("-weight", "name")[:limit]) if prefix else []

    everything = Place.objects.all()
    if "," in query:
        city, _, state_part = query.partition(",")
        states = _states_matching(state_part)
        rows = search(everything.filter(state__in=states) if states is not None else everything, normalise(city))
    else:
        # Whole text first ("new or" -> New Orleans); only if nothing matches read a trailing
        # state ("austin tx" -> Austin in TX). Otherwise "or"/"in"/"me" would always mean a state.
        rows = search(everything, normalise(query))
        if not rows and (parsed := split_city_state(query)):
            rows = search(everything.filter(state=parsed[1]), normalise(parsed[0]))

    results = [{"label": f"{p.name}, {p.state}", "name": p.name, "state": p.state,
                "lat": round(p.lat, 5), "lon": round(p.lon, 5)} for p in rows]
    cache.set(cache_key, results, timeout=60 * 60)
    return results
