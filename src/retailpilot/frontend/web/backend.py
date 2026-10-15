"""Server-side client for the FastAPI agent backend (src/retailpilot/backend/agent_route.py)."""

from typing import Any

import httpx
from django.conf import settings

TIMEOUT = 180


class BackendError(Exception):
    """The backend answered with an error or could not be reached."""


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        with httpx.Client(base_url=settings.RETAILPILOT_BACKEND_URL, timeout=TIMEOUT) as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise BackendError(f"Cannot reach the RetailPilot backend at {settings.RETAILPILOT_BACKEND_URL}.") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise BackendError(str(detail))
    return response.json()


def get(path: str, **params: Any) -> Any:
    return _request("GET", path, params={k: v for k, v in params.items() if v is not None})


def post(path: str, body: dict[str, Any]) -> Any:
    return _request("POST", path, json=body)
