"""Shared HTTP session (connection pooling + keep-alive saves a TLS handshake per call)."""

import threading

import requests
from django.conf import settings

_local = threading.local()


class ExternalServiceError(Exception):
    """An upstream (routing / geocoding) service failed or returned garbage."""


def session():
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.headers["User-Agent"] = settings.FUEL_PLANNER["HTTP_USER_AGENT"]
        _local.session = s
    return _local.session


def get_json(url, params=None):
    try:
        resp = session().get(url, params=params, timeout=settings.FUEL_PLANNER["HTTP_TIMEOUT_SECONDS"])
    except requests.RequestException as exc:
        raise ExternalServiceError(f"Request to {url} failed: {exc}") from exc
    if resp.status_code >= 500 or resp.status_code == 429:
        raise ExternalServiceError(f"{url} returned HTTP {resp.status_code}")
    try:
        return resp.status_code, resp.json()
    except ValueError as exc:
        raise ExternalServiceError(f"{url} returned non-JSON response") from exc
