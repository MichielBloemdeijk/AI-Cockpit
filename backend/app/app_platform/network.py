"""`cockpit.fetch` for app functions: HTTP limited to the hosts the app declares.

The allowlist is the manifest's `grants.network`. Addresses that are not
public (loopback, private, link-local) are refused even when a granted name
resolves to them, so an app cannot reach Cockpit's own API or the local
network. Redirects are followed by hand so every hop is checked.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
BODY_LIMIT_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_TIMEOUT_SECONDS = 60
DROPPED_REQUEST_HEADERS = frozenset({"host", "content-length", "connection", "transfer-encoding"})


class NetworkError(ValueError):
    pass


def host_allowed(host: str, grants: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in grants:
        if pattern.startswith("*."):
            domain = pattern[2:]
            if host == domain or host.endswith("." + domain):
                return True
        elif host == pattern:
            return True
    return False


def check_url(url: str, grants: list[str]) -> str:
    """The URL's host when it may be fetched; raises NetworkError otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise NetworkError("Only http and https URLs can be fetched")
    if parts.username or parts.password:
        raise NetworkError("URLs with credentials are not allowed")
    if not host_allowed(parts.hostname, grants):
        raise NetworkError(f"{parts.hostname} is not in the app's network grants; add it to grants.network")
    return parts.hostname


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=0)
    return [info[4][0] for info in infos]


async def _check_addresses(host: str, port: int) -> None:
    try:
        addresses = await _resolve(host, port)
    except OSError as exc:
        raise NetworkError(f"Cannot resolve {host}: {exc}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise NetworkError(f"{host} resolves to a non-public address; apps can only reach the internet")


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=False, timeout=timeout, trust_env=False)


async def fetch(grants: list[str], *, url: str, method: str = "GET", headers: dict[str, str] | None = None,
                body: bytes | None = None, timeout: float = 20) -> dict[str, Any]:
    method = str(method).upper()
    if method not in METHODS:
        raise NetworkError(f"Unsupported method {method}")
    if body is not None and len(body) > BODY_LIMIT_BYTES:
        raise NetworkError("Request bodies are at most 5 MiB")
    sent_headers = {str(key): str(value) for key, value in (headers or {}).items()
                    if str(key).lower() not in DROPPED_REQUEST_HEADERS}
    timeout = max(1.0, min(float(timeout or 20), MAX_TIMEOUT_SECONDS))
    async with _client(timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            host = check_url(url, grants)
            parts = urlsplit(url)
            await _check_addresses(host, parts.port or (443 if parts.scheme == "https" else 80))
            try:
                async with client.stream(method, url, headers=sent_headers, content=body) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        if response.status_code in {301, 302, 303} and method != "HEAD":
                            method, body = "GET", None
                        continue
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > BODY_LIMIT_BYTES:
                            raise NetworkError("Response is larger than 5 MiB")
                    return {"status": response.status_code, "url": url,
                            "headers": {key: value for key, value in response.headers.items()},
                            "body_base64": base64.b64encode(bytes(data)).decode()}
            except httpx.HTTPError as exc:
                raise NetworkError(f"Request failed: {type(exc).__name__}: {exc}") from exc
    raise NetworkError("Too many redirects")
