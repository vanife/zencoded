"""SSRF-safe URL downloader.

The web service downloads arbitrary operator-supplied URLs, so this module defends
against server-side request forgery:

* only ``http`` / ``https`` schemes are allowed;
* the hostname is resolved and **every** resulting IP is checked — the request is
  rejected if any maps to a loopback/private/link-local/reserved/multicast address
  (this covers the cloud metadata endpoint ``169.254.169.254``);
* redirects are followed manually and the target is re-validated on every hop, so a
  public URL cannot bounce to an internal one;
* the body is streamed to disk with a hard size cap and request timeouts.

Residual risk: a tiny TOCTOU window exists between our DNS resolution and the
connection's own resolution (DNS rebinding). For this single-operator tool the
per-hop validation above is the accepted mitigation.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_FILENAME = "download.bin"


class DownloadError(Exception):
    """Raised when a URL is unsafe or the download fails/violates a limit."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    filename: str
    size: int
    final_url: str
    content_type: str | None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_and_check(host: str, port: int) -> None:
    """Resolve ``host`` and raise if any resolved address is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DownloadError(f"cannot resolve host {host!r}: {exc}") from exc
    if not infos:
        raise DownloadError(f"host {host!r} did not resolve to any address")
    for info in infos:
        sockaddr = info[4]
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(ip):
            raise DownloadError(
                f"refusing to connect to non-public address {ip} (host {host!r})"
            )


def validate_url(url: str) -> httpx.URL:
    """Validate scheme/host and check that the host resolves to a public IP."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise DownloadError(
            f"unsupported URL scheme {parsed.scheme!r} (allowed: http, https)"
        )
    if not parsed.hostname:
        raise DownloadError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _resolve_and_check(parsed.hostname, port)
    return httpx.URL(url)


def _sanitize_filename(name: str) -> str:
    name = Path(unquote(name)).name.strip().strip(".")
    # Drop path separators / NUL just in case, keep it simple and safe.
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    return name or _DEFAULT_FILENAME


def _filename_from_response(resp: httpx.Response) -> str:
    disposition = resp.headers.get("content-disposition", "")
    # Look for filename*=UTF-8''... then filename="..."
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            value = part.split("=", 1)[1]
            if "''" in value:
                value = value.split("''", 1)[1]
            return _sanitize_filename(value.strip('"'))
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return _sanitize_filename(part.split("=", 1)[1].strip('"'))
    path_name = Path(urlparse(str(resp.url)).path).name
    return _sanitize_filename(path_name) if path_name else _DEFAULT_FILENAME


async def download(
    url: str,
    dest_dir: str | Path,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    max_redirects: int = 5,
) -> DownloadResult:
    """Safely download ``url`` into ``dest_dir`` and return a :class:`DownloadResult`."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    current = validate_url(url)
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout
    ) as client:
        for _hop in range(max_redirects + 1):
            async with client.stream("GET", current) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise DownloadError("redirect response without Location header")
                    current = validate_url(str(resp.url.join(location)))
                    continue

                resp.raise_for_status()

                # Reject early if the server advertises an oversized body.
                declared = resp.headers.get("content-length")
                if declared is not None and declared.isdigit():
                    if int(declared) > max_bytes:
                        raise DownloadError(
                            f"file too large: {declared} bytes > limit {max_bytes}"
                        )

                filename = _filename_from_response(resp)
                target = dest_dir / filename
                size = 0
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            fh.close()
                            target.unlink(missing_ok=True)
                            raise DownloadError(
                                f"file exceeded size limit of {max_bytes} bytes"
                            )
                        fh.write(chunk)

                return DownloadResult(
                    path=target,
                    filename=filename,
                    size=size,
                    final_url=str(resp.url),
                    content_type=resp.headers.get("content-type"),
                )

    raise DownloadError(f"too many redirects (> {max_redirects})")
