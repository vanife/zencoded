# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

`zencoded` encodes files as **self-extracting base64 Python scripts** (committed under
`data/`) so binaries can travel over the internet as plain text. A cloud-hosted FastAPI
service lets an authenticated operator submit a URL; the server downloads it, encodes it,
and commits + pushes the generated script to this repository.

## Commands

`uv`-managed project, Python ≥ 3.13.

- Install: `uv sync`
- Tests: `uv run pytest` — single test: `uv run pytest tests/test_encoder.py::test_round_trip`
- CLI: `uv run zencoded encode ./file.bin` / `uv run zencoded encode-url https://…`
- Web service: `uv run uvicorn zencoded.web.app:app --reload` (needs `.env`, see `.env.example`)
- Run a generated extractor: `python data/<name>.py [-o DIR] [--force]`

## Architecture

The job pipeline is **download → encode → publish**, orchestrated by `jobs.run_job` and
triggered either by the CLI or the web API. Key modules in `src/zencoded/`:

- `encoder.py` + `template.py` — core. `encode_file/encode_bytes` produce an
  `EncodeResult` whose `.script` is rendered from `template.TEMPLATE` via plain token
  substitution (not `str.format`, so the template may contain `{`/`}`). Compression is
  `auto|always|never`; `auto` keeps gzip only when it shrinks the data (already-compressed
  inputs stay raw). The generated script is **stdlib-only** and verifies SHA-256 on extract.
- `downloader.py` — SSRF-safe fetch. `validate_url` resolves the host and rejects any
  non-public IP; redirects are followed manually and **re-validated per hop**. Streams to
  `temp/<job>/` with a size cap. Edit this module carefully — its guarantees are the
  service's main attack surface.
- `publisher.py` — git publishing via subprocess. Writes only under `data/`
  (`_resolve_within` blocks traversal), then `add → commit → pull --rebase → push` using a
  deploy key passed through `GIT_SSH_COMMAND`. Honors `publish_enabled=False` for dev.
- `releaser.py` — alternative publish path for files over GitHub's 100 MiB push limit:
  uploads the script as a GitHub Release asset via the REST API (httpx). Needs a
  `github_token` (Contents:write) — the deploy key can't call the REST API. `jobs.run_job`
  picks git vs release via `settings.resolve_publish_mode(script_size)` (`git`/`release`/`auto`).
- `jobs.py` — in-memory `JobRegistry` (thread-safe) + `run_job` (async; runs blocking git
  in a thread, cleans up `temp/`).
- `config.py` — `Settings` (pydantic-settings, env prefix `ZENCODED_`). Use the cached
  `get_settings()`. `data_dir`/`temp_dir`/`repo_dir` default relative to the repo root.
- `web/` — `app.py` (routes, `SessionMiddleware`, rate limiting, security headers),
  `auth.py` (GitHub OAuth + allowlist `require_user`, `verify_csrf`), `models.py` (schemas).

## Conventions & gotchas

- The security model is the point of this project — read `docs/SECURITY.md` before
  touching `downloader.py`, `publisher.py`, or `web/auth.py`, and keep their defenses
  intact (SSRF checks, path-traversal guard, deny-by-default auth, CSRF).
- gzip uses `mtime=0` so identical input yields byte-identical scripts (stable git diffs).
- `temp/` is gitignored and is scratch only; generated scripts belong in `data/`.
- `data/` collisions are overwritten by design (git history retains prior versions).
