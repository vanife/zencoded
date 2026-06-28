"""Publish generated scripts as GitHub Release assets (for large files).

Files over GitHub's hard 100 MiB per-file push limit cannot be committed to the repo, so
the encoded script is uploaded as a Release asset instead (assets allow up to 2 GiB and
do not count against repo size). The asset's ``browser_download_url`` serves the raw
bytes, so the script stays fetchable as plain text — e.g. ``curl -O <url>``.

Unlike git push (SSH deploy key), the Releases REST API needs a **token** with the
repo's Contents:write permission (a fine-grained PAT scoped to this repo, or a GitHub App
installation token). Provide it via ``Settings.github_token``.

The upload is idempotent per asset name: an existing asset with the same name is deleted
first (assets are immutable), matching the project's overwrite semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import httpx

from .config import Settings

_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB streaming chunks


class ReleaseError(Exception):
    """Raised when uploading a release asset fails or is misconfigured."""


@dataclass(frozen=True)
class ReleaseResult:
    name: str
    size: int
    uploaded: bool
    download_url: Optional[str]
    tag: str


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while True:
            block = fh.read(_UPLOAD_CHUNK)
            if not block:
                return
            yield block


def _require_config(settings: Settings) -> None:
    missing = [
        name
        for name, value in (
            ("github_token", settings.github_token),
            ("github_owner", settings.github_owner),
            ("github_repo", settings.github_repo),
        )
        if not value
    ]
    if missing:
        raise ReleaseError(
            "release publishing requires " + ", ".join(missing) + " to be configured"
        )


def _get_or_create_release(client: httpx.Client, settings: Settings) -> dict:
    base = f"{settings.github_api_url}/repos/{settings.github_owner}/{settings.github_repo}"
    resp = client.get(f"{base}/releases/tags/{settings.release_tag}")
    if resp.status_code == 404:
        resp = client.post(
            f"{base}/releases",
            json={"tag_name": settings.release_tag, "name": settings.release_tag},
        )
    if resp.status_code >= 400:
        raise ReleaseError(f"could not get/create release: {resp.status_code} {resp.text}")
    return resp.json()


def upload_asset(
    settings: Settings,
    *,
    path: Path,
    client: Optional[httpx.Client] = None,
) -> ReleaseResult:
    """Upload ``path`` as an asset on the configured rolling release (overwriting)."""
    path = Path(path)
    size = path.stat().st_size
    name = path.name

    if not settings.publish_enabled:
        return ReleaseResult(name, size, uploaded=False, download_url=None, tag=settings.release_tag)

    _require_config(settings)
    base = f"{settings.github_api_url}/repos/{settings.github_owner}/{settings.github_repo}"

    owns_client = client is None
    client = client or httpx.Client(headers=_headers(settings), timeout=300.0)
    try:
        release = _get_or_create_release(client, settings)

        # Assets are immutable; delete an existing same-named asset to "overwrite".
        for asset in release.get("assets", []):
            if asset.get("name") == name:
                resp = client.delete(f"{base}/releases/assets/{asset['id']}")
                if resp.status_code >= 400:
                    raise ReleaseError(
                        f"could not delete existing asset {name}: {resp.status_code}"
                    )

        # upload_url is templated like ".../assets{?name,label}" — strip the template.
        upload_url = release["upload_url"].split("{")[0]
        resp = client.post(
            upload_url,
            params={"name": name},
            content=_file_chunks(path),
            headers={
                **_headers(settings),
                "Content-Type": "text/x-python",
                "Content-Length": str(size),
            },
        )
        if resp.status_code >= 400:
            raise ReleaseError(f"asset upload failed: {resp.status_code} {resp.text}")
        download_url = resp.json().get("browser_download_url")
        return ReleaseResult(name, size, uploaded=True, download_url=download_url, tag=settings.release_tag)
    finally:
        if owns_client:
            client.close()
