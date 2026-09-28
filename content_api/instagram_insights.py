"""Read lifetime insights for one of our published Instagram media objects."""

from __future__ import annotations

import os
import re

import requests

METRICS = ("views", "reach", "likes", "comments", "saved", "shares")
VERSION = re.compile(r"v\d+\.\d+\Z")


class InsightsError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def config() -> tuple[str, str]:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise InsightsError("token_missing")
    if not VERSION.fullmatch(version):
        raise InsightsError("invalid_api_version")
    return token, version


def _read_response(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise InsightsError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise InsightsError("invalid_response")
    if response.status_code >= 400 or payload.get("error"):
        error = payload.get("error") or {}
        code = error.get("code") if isinstance(error, dict) else None
        if response.status_code == 429 or code in (4, 17, 32, 613):
            raise InsightsError("rate_limited")
        if code == 100:
            raise InsightsError("unsupported_metric")
        if code in (10, 190, 200):
            raise InsightsError("permission_or_token")
        raise InsightsError(f"graph_http_{response.status_code}")
    return payload


def _values(payload: dict) -> dict[str, int]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise InsightsError("invalid_response")
    result: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("name") not in METRICS:
            continue
        total = item.get("total_value")
        value = total.get("value") if isinstance(total, dict) else None
        if value is None and isinstance(item.get("values"), list) and item["values"]:
            first = item["values"][0]
            value = first.get("value") if isinstance(first, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[item["name"]] = value
    return result


def fetch_metrics(
    session: requests.Session, media_id: str, token: str, version: str
) -> dict[str, int | None]:
    url = f"https://graph.instagram.com/{version}/{media_id}/insights"
    headers = {"Authorization": f"Bearer {token}"}

    def request(names: tuple[str, ...]) -> dict[str, int]:
        try:
            response = session.get(
                url, params={"metric": ",".join(names)}, headers=headers,
                timeout=(5, 20),
            )
        except requests.RequestException as exc:
            raise InsightsError("network_error") from exc
        return _values(_read_response(response))

    try:
        values = request(METRICS)
    except InsightsError as exc:
        if exc.code != "unsupported_metric":
            raise
        # A single unsupported metric can reject the entire combined request.
        values = {}
        for name in METRICS:
            try:
                values.update(request((name,)))
            except InsightsError as metric_error:
                if metric_error.code != "unsupported_metric":
                    raise
    if not values:
        raise InsightsError("metrics_unavailable")
    return {name: values.get(name) for name in METRICS}
