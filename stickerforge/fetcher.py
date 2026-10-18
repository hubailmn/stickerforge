"""Fetch sticker source images from URLs or local files."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import unquote, urlparse

import requests

from . import config


class FetchError(Exception):
    """Raised when a source cannot be retrieved or is rejected."""


def _guard_host(host: str) -> None:
    if config.ALLOW_PRIVATE_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host: {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise FetchError(f"blocked private/link-local address: {address}")


def _read_local(path: str, max_bytes: int) -> bytes:
    if not os.path.isfile(path):
        raise FetchError(f"file not found: {path}")
    if os.path.getsize(path) > max_bytes:
        raise FetchError(f"file too large (limit {max_bytes} bytes): {path}")
    with open(path, "rb") as handle:
        return handle.read()


def fetch(
    source: str,
    timeout: int = config.FETCH_TIMEOUT,
    max_bytes: int = config.MAX_DOWNLOAD_BYTES,
) -> bytes:
    """Return the raw bytes for a URL, file:// URL, or local path."""
    source = source.strip()
    if not source:
        raise FetchError("empty source")

    parsed = urlparse(source)
    if parsed.scheme == "file":
        return _read_local(unquote(parsed.path), max_bytes)
    if parsed.scheme == "":
        return _read_local(source, max_bytes)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"unsupported scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise FetchError(f"invalid URL: {source}")

    _guard_host(parsed.hostname)
    try:
        response = requests.get(
            source,
            timeout=timeout,
            stream=True,
            headers={"User-Agent": config.USER_AGENT},
        )
    except requests.RequestException as exc:
        raise FetchError(f"request failed: {exc}") from exc

    with response:
        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code} for {source}")
        chunks = []
        total = 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise FetchError(f"remote file exceeds {max_bytes} bytes: {source}")
            chunks.append(chunk)
    if not chunks:
        raise FetchError(f"empty response body: {source}")
    return b"".join(chunks)


def is_remote(source: str) -> bool:
    return urlparse(source.strip()).scheme in ("http", "https")
