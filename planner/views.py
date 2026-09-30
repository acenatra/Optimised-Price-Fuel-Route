import json
import logging
from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .services.geocoding import LocationError
from .services.http import ExternalServiceError
from .services.planner import PlanningError, PlanRequest, plan_trip
from .services.routing import RouteNotFound
from .services.station_index import StationIndex
from .services.suggest import suggest

log = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}


def _parse(params):
    start = (params.get("start") or "").strip()
    finish = (params.get("finish") or params.get("end") or "").strip()
    missing = [k for k, v in (("start", start), ("finish", finish)) if not v]
    if missing:
        raise LocationError(f"Missing required parameter(s): {', '.join(missing)}.")
    return PlanRequest(
        start=start,
        finish=finish,
        start_full=str(params.get("start_full", "false")).lower() in _TRUE,
        include_geometry=str(params.get("geometry", "true")).lower() in _TRUE,
        stop_penalty=_float_or_none(params.get("stop_penalty")),
    )


def _float_or_none(value):
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise LocationError("stop_penalty must be a number.") from None
    if not 0 <= v <= 1000:
        raise LocationError("stop_penalty must be between 0 and 1000.")
    return v


def _run(req):
    """Returns (status, payload)."""
    try:
        return 200, plan_trip(req)
    except LocationError as exc:
        return 400, {"error": "invalid_location", "detail": str(exc)}
    except (PlanningError, RouteNotFound) as exc:
        return 422, {"error": "no_plan", "detail": str(exc)}
    except ExternalServiceError as exc:
        log.warning("Upstream failure: %s", exc)
        return 502, {"error": "upstream_unavailable", "detail": str(exc)}


@method_decorator(csrf_exempt, name="dispatch")
class RouteView(View):
    """GET /api/v1/route/?start=Chicago, IL&finish=Denver, CO[&start_full=true][&geometry=false]
    POST /api/v1/route/ {"start": "...", "finish": "...", "start_full": false}
    """

    def get(self, request):
        return self._respond(request, request.GET)

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return JsonResponse({"error": "invalid_json", "detail": "Body must be a JSON object."}, status=400)
        return self._respond(request, body)

    def _respond(self, request, params):
        try:
            req = _parse(params)
        except LocationError as exc:
            return JsonResponse({"error": "invalid_request", "detail": str(exc)}, status=400)
        status, payload = _run(req)
        if status == 200:
            qs = {"start": req.start, "finish": req.finish, "start_full": str(req.start_full).lower()}
            if req.stop_penalty is not None:
                qs["stop_penalty"] = req.stop_penalty
            qs = urlencode(qs)
            payload["map_url"] = request.build_absolute_uri(f"{reverse('planner:map')}?{qs}")
        return JsonResponse(payload, status=status)


class MapView(View):
    """Interactive Leaflet map of the route and fuel stops (reuses the cached plan)."""

    def get(self, request):
        ctx = {"start": request.GET.get("start", ""), "finish": request.GET.get("finish", ""),
               "start_full": request.GET.get("start_full", "false").lower() in _TRUE}
        status = 200
        if ctx["start"] or ctx["finish"]:
            try:
                req = _parse(request.GET)
                status, payload = _run(PlanRequest(req.start, req.finish, req.start_full, True, req.stop_penalty))
            except LocationError as exc:
                status, payload = 400, {"error": "invalid_request", "detail": str(exc)}
            ctx["plan" if status == 200 else "error"] = payload
        return render(request, "planner/map.html", ctx, status=status)


def health(request):
    return JsonResponse({"status": "ok", "stations_loaded": len(StationIndex.get())})


def suggest_view(request):
    """GET /api/v1/suggest/?q=chi -> [{"label": "Chicago, IL", "lat": .., "lon": ..}, ...]"""
    return JsonResponse({"results": suggest(request.GET.get("q", ""))})
