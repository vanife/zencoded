"""Publish generated scripts to the git repository (least-privilege).

A new self-extractor script is written under ``data/`` and committed + pushed using an
SSH **deploy key** scoped to this single repository, so a compromise of the service
cannot reach any other repo or the wider GitHub account.

Defenses here:

* the written path is verified to stay inside ``data/`` (no path traversal);
* git talks to GitHub over SSH via ``GIT_SSH_COMMAND`` with ``IdentitiesOnly=yes`` and a
  pinned ``known_hosts`` (no agent keys, no host-key TOFU);
* a ``pull --rebase`` precedes the push and pushes are never forced.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


class PublishError(Exception):
    """Raised when writing or pushing a generated script fails."""


@dataclass(frozen=True)
class PublishResult:
    path: Path
    committed: bool
    pushed: bool
    commit_message: str


def _resolve_within(base: Path, name: str) -> Path:
    """Return ``base/name`` ensuring the result stays within ``base``."""
    base = base.resolve()
    candidate = (base / name).resolve()
    if candidate.parent != base:
        raise PublishError(f"refusing path outside data dir: {name!r}")
    return candidate


def _git_env(settings: Settings) -> dict[str, str]:
    import os

    env = dict(os.environ)
    if settings.deploy_key_path:
        parts = [
            "ssh",
            "-i",
            str(settings.deploy_key_path),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if settings.known_hosts_path:
            parts += ["-o", f"UserKnownHostsFile={settings.known_hosts_path}"]
        env["GIT_SSH_COMMAND"] = " ".join(parts)
    # Make commit identity deterministic regardless of host git config.
    env["GIT_AUTHOR_NAME"] = settings.git_author_name
    env["GIT_AUTHOR_EMAIL"] = settings.git_author_email
    env["GIT_COMMITTER_NAME"] = settings.git_author_name
    env["GIT_COMMITTER_EMAIL"] = settings.git_author_email
    return env


def _git(settings: Settings, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(settings.repo_dir), *args],
        env=_git_env(settings),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise PublishError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def write_script(settings: Settings, filename: str, script: str) -> Path:
    """Write the generated script into ``data/`` (overwriting), returning its path."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    target = _resolve_within(settings.data_dir, filename)
    target.write_text(script, encoding="utf-8")
    return target


def publish(
    settings: Settings,
    *,
    filename: str,
    script: str,
    source_url: str,
    actor: str,
    job_id: str,
) -> PublishResult:
    """Write ``script`` to ``data/<filename>`` and commit + push it."""
    target = write_script(settings, filename, script)
    message = (
        f"Add encoded {filename}\n\n"
        f"Source: {source_url}\n"
        f"Actor: {actor}\n"
        f"Job: {job_id}\n"
    )

    if not settings.publish_enabled:
        return PublishResult(target, committed=False, pushed=False, commit_message=message)

    rel = target.relative_to(Path(settings.repo_dir).resolve())
    _git(settings, "add", "--", str(rel))

    # Nothing staged (identical content already committed) -> skip the commit/push.
    status = _git(settings, "status", "--porcelain", "--", str(rel))
    if not status.strip():
        return PublishResult(target, committed=False, pushed=False, commit_message=message)

    _git(settings, "commit", "-m", message)
    # Integrate concurrent commits before pushing; never force.
    _git(settings, "pull", "--rebase", settings.git_remote, settings.git_branch)
    _git(settings, "push", settings.git_remote, f"HEAD:{settings.git_branch}")

    return PublishResult(target, committed=True, pushed=True, commit_message=message)
