"""Pre-load the URLconf and station index so the first request is as fast as the rest."""

import logging

log = logging.getLogger(__name__)


def warm_up():
    try:
        from django.urls import get_resolver

        from .services.station_index import StationIndex

        get_resolver().url_patterns  # imports views -> numpy/scipy
        log.info("Station index warmed: %d stations", len(StationIndex.get()))
    except Exception:  # DB not migrated/loaded yet: requests will report it clearly.
        log.warning("Warm-up skipped", exc_info=True)
