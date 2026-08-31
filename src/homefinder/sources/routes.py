"""Small Google Routes REST adapter; policy and quota stay in the application layer."""

import json
from datetime import timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from homefinder.domain.routing import RouteObservation, TravelMode
from homefinder.routing.service import RouteQuery


class ProviderQuotaError(RuntimeError):
    pass


class GoogleRoutesProvider:
    endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"

    def __init__(self, api_key: str, timeout_seconds: float = 10.0) -> None:
        if not api_key:
            raise ValueError("Google Routes API key is required")
        self._api_key = api_key
        self._timeout = timeout_seconds

    def route(self, query: RouteQuery) -> RouteObservation:
        payload: dict[str, object] = {
            "origin": {"address": query.origin},
            "destination": {"placeId": query.destination_place_id},
            "travelMode": query.mode.value.upper(),
            "routingPreference": "TRAFFIC_AWARE"
            if query.mode is TravelMode.DRIVE
            else "ROUTING_PREFERENCE_UNSPECIFIED",
            "computeAlternativeRoutes": False,
        }
        if query.mode in {TravelMode.DRIVE, TravelMode.TRANSIT}:
            payload["departureTime"] = (
                query.departure_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        request = Request(  # noqa: S310 - endpoint is a fixed HTTPS constant
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": "routes.duration",
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
        return RouteObservation(
            query.mode,
            max(1, (seconds + 59) // 60),
            "google_routes",
            query.departure_at,
            1.0,
        )
