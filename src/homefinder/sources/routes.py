"""Google Routes REST adapter; policy and durable quota stay outside."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from homefinder.domain.routing import RouteObservation, TravelMode
from homefinder.routing.service import RouteQuery, RouteTimeSemantics, Waypoint


class ProviderQuotaError(RuntimeError):
    pass


class GoogleRoutesProvider:
    endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"
    name = "google_routes"

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = 10.0,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Google Routes API key is required")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def supports(self, query: RouteQuery) -> bool:
        return not (
            query.mode is TravelMode.DRIVE
            and query.time_semantics is RouteTimeSemantics.ARRIVAL
        )

    def route(self, query: RouteQuery) -> RouteObservation:
        if not self.supports(query):
            raise ValueError("requested mode does not support arrival semantics")
        payload: dict[str, object] = {
            "origin": _waypoint(query.origin),
            "destination": _waypoint(query.destination),
            "travelMode": query.mode.value.upper(),
            "computeAlternativeRoutes": False,
        }
        if query.mode is TravelMode.DRIVE:
            payload["routingPreference"] = "TRAFFIC_AWARE"
        if query.mode in {TravelMode.DRIVE, TravelMode.TRANSIT}:
            field = (
                "arrivalTime"
                if query.time_semantics is RouteTimeSemantics.ARRIVAL
                else "departureTime"
            )
            payload[field] = (
                query.requested_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        request = Request(  # noqa: S310 - endpoint is a fixed HTTPS constant
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": "routes.duration,routes.travelAdvisory",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = json.load(response)
        except HTTPError as error:
            if error.code in {403, 429}:
                raise ProviderQuotaError(
                    "Google Routes provider quota exhausted"
                ) from error
            raise RuntimeError(
                f"Google Routes request failed ({error.code})"
            ) from error
        except URLError as error:
            raise RuntimeError(
                "Google Routes request could not be completed"
            ) from error
        try:
            seconds = int(str(body["routes"][0]["duration"]).removesuffix("s"))
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError("Google Routes response had no usable route") from error
        advisories = (
            ("duration is approximate for non-motorized mode",)
            if query.mode in {TravelMode.WALK, TravelMode.BICYCLE}
            else ()
        )
        return RouteObservation(
            query.mode,
            max(1, (seconds + 59) // 60),
            self.name,
            self._clock(),
            1.0,
            advisories,
        )


def _waypoint(value: Waypoint) -> dict[str, str]:
    return {"placeId" if value.kind == "place_id" else "address": value.value}
