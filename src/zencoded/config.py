"""Application configuration, loaded from environment variables / ``.env``.

All secrets and host-specific paths live here so nothing sensitive is hard-coded.
Import :func:`get_settings` (cached) rather than instantiating ``Settings`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .encoder import CompressMode

# How a generated script is published:
#   "git"     -> commit + push the file into data/ (default; fails on >100 MiB files)
#   "release" -> upload the file as a GitHub Release asset (no size limit, needs a token)
#   "auto"    -> release when the script exceeds release_size_threshold, else git
PublishMode = Literal["git", "release", "auto"]

# Repo root = two levels up from this file (src/zencoded/config.py -> repo/).
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZENCODED_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- GitHub OAuth (operator login) ---
    github_client_id: str = ""
    github_client_secret: str = ""
    # Comma-separated GitHub logins allowed to use the service. Empty => nobody.
    oauth_allowlist: list[str] = Field(default_factory=list)

    # --- Web session ---
    session_secret: str = "change-me-in-production"
    session_max_age: int = 60 * 60 * 8  # seconds
    # Public base URL of the service (used to build the OAuth redirect URI).
    base_url: str = "http://localhost:8000"
    # Set False only for local non-TLS development.
    secure_cookies: bool = True

    # --- Git publishing ---
    repo_dir: Path = _REPO_ROOT
    git_branch: str = "main"
    git_remote: str = "origin"
    git_author_name: str = "zencoded-bot"
    git_author_email: str = "zencoded-bot@users.noreply.github.com"
    deploy_key_path: Path | None = None
    known_hosts_path: Path | None = None
    # If False, files are written to data/ but not committed/pushed/uploaded (dev).
    publish_enabled: bool = True

    # --- GitHub Releases publishing (for large files) ---
    publish_mode: PublishMode = "git"
    # Token with this repo's Contents:write permission (fine-grained PAT or App token).
    # Required for "release"/"auto" modes — the SSH deploy key cannot call the REST API.
    github_token: str = ""
    # owner/repo the releases belong to (e.g. "octocat" / "zencoded").
    github_owner: str = ""
    github_repo: str = ""
    # Single rolling release tag that encoded assets are attached to.
    release_tag: str = "encoded"
    github_api_url: str = "https://api.github.com"
    # In "auto" mode, scripts larger than this go to a Release instead of git.
    # 90 MiB leaves headroom under GitHub's hard 100 MiB per-file push limit.
    release_size_threshold: int = 90 * 1024 * 1024

    # --- Download / encoding ---
    data_dir: Path = _REPO_ROOT / "data"
    temp_dir: Path = _REPO_ROOT / "temp"
    max_download_bytes: int = 100 * 1024 * 1024  # 100 MiB
    download_timeout: float = 30.0
    max_redirects: int = 5
    default_compress: CompressMode = "never"

    # --- Rate limiting (job creation) ---
    rate_limit_jobs: int = 10
    rate_limit_window: int = 60  # seconds

    @field_validator("oauth_allowlist", mode="before")
    @classmethod
    def _split_allowlist(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("oauth_allowlist", mode="after")
    @classmethod
    def _normalize_allowlist(cls, v: list[str]) -> list[str]:
        # GitHub logins are case-insensitive; store lowercased for comparison.
        return [item.lower() for item in v]

    def is_allowed(self, github_login: str) -> bool:
        return github_login.lower() in self.oauth_allowlist

    def resolve_publish_mode(self, script_size: int) -> str:
        """Resolve "auto" to "git"/"release" based on the generated script's size."""
        if self.publish_mode != "auto":
            return self.publish_mode
        return "release" if script_size > self.release_size_threshold else "git"


@lru_cache
def get_settings() -> Settings:
    return Settings()
