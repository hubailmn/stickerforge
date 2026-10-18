"""Fetch sticker source images from URLs or local files."""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from urllib.parse import unquote, urljoin, urlparse

import requests

from . import config

MAX_REDIRECTS = 5
_local = threading.local()


class FetchError(Exception):
    """Raised when a source cannot be retrieved or is rejected."""


def _session() -> requests.Session:
    """One pooled session per thread (sessions are not safe to share across threads)."""
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
    return session


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


def _get_following_redirects(source: str, timeout: int, max_bytes: int) -> bytes:
    """GET with redirects followed manually so every hop passes the host guard.

    requests' automatic redirects would let a public URL bounce the server into
    a private address without being checked.
    """
    session = _session()
    url = source
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise FetchError(f"invalid redirect target: {url}")
        _guard_host(parsed.hostname)
        try:
            response = session.get(
                url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": config.USER_AGENT},
            )
        except requests.RequestException as exc:
            raise FetchError(f"request failed: {exc}") from exc

        with response:
            if response.is_redirect and "Location" in response.headers:
                url = urljoin(url, response.headers["Location"])
                continue
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} for {source}")
            return _read_capped(response, max_bytes, source)
    raise FetchError(f"too many redirects: {source}")


def _read_capped(response: requests.Response, max_bytes: int, source: str) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"remote file exceeds {max_bytes} bytes: {source}")
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


def fetch(
    source: str,
    timeout: int = config.FETCH_TIMEOUT,
    max_bytes: int = config.MAX_DOWNLOAD_BYTES,
    allow_local: bool = True,
) -> bytes:
    """Return the raw bytes for a URL, file:// URL, or local path.

    Pass allow_local=False for untrusted input (the web app) so only http(s) is read.
    """
    source = source.strip()
    if not source:
        raise FetchError("empty source")

    parsed = urlparse(source)
    if parsed.scheme in ("file", "") and not allow_local:
        raise FetchError("only http(s) URLs are accepted")
    if parsed.scheme == "file":
        return _read_local(unquote(parsed.path), max_bytes)
    if parsed.scheme == "":
        return _read_local(source, max_bytes)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"unsupported scheme '{parsed.scheme}'")
    if not parsed.hostname:
        raise FetchError(f"invalid URL: {source}")

    return _get_following_redirects(source, timeout, max_bytes)


def is_remote(source: str) -> bool:
    return urlparse(source.strip()).scheme in ("http", "https")
