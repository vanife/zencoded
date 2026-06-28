# Security model

zencoded runs a cloud-hosted service that, on operator request, downloads an arbitrary
URL and publishes a generated script into this git repository. Three trust boundaries
are defended: **who can trigger jobs**, **what the server is allowed to fetch**, and
**what the publishing job can write to GitHub**.

## A. Authorizing who can trigger downloads

- **GitHub OAuth, deny-by-default.** Only GitHub logins listed in
  `ZENCODED_OAUTH_ALLOWLIST` may sign in; the allowlist can be a single person. See
  `web/auth.py` (`callback` enforces `Settings.is_allowed`).
- **Login CSRF** is prevented by the OAuth `state` parameter (handled by Authlib and
  stored in the signed session).
- **Sessions** are signed cookies (`SessionMiddleware`): `HttpOnly`, `SameSite=Lax`,
  `Secure` when `ZENCODED_SECURE_COOKIES=true`, with a bounded `max_age`. The signing
  key (`ZENCODED_SESSION_SECRET`) must be a strong random value from a secret store.
- **Request CSRF** for the state-changing `POST /jobs`: `verify_csrf` requires the
  `Origin`/`Referer` to match the service host **and** a non-simple `X-Requested-With:
  zencoded` header, which a cross-site HTML form cannot send. `SameSite=Lax` is a second
  layer.
- **Rate limiting** caps job creation per operator (`ZENCODED_RATE_LIMIT_*`).
- **Transport**: terminate TLS upstream; `Strict-Transport-Security` and other hardening
  headers are set by the `security_headers` middleware.

## B. Download safety (SSRF / abuse) — `downloader.py`

- **Scheme allowlist**: only `http`/`https`.
- **Private/metadata IP block**: the hostname is resolved and *every* resolved address
  is rejected if loopback/private/link-local/reserved/multicast/unspecified — this
  blocks `127.0.0.1`, `10/8`, `192.168/16`, `::1`, and the cloud metadata endpoint
  `169.254.169.254`.
- **Per-redirect re-validation**: redirects are followed manually and each hop is
  re-validated, so a public URL cannot bounce into an internal one.
- **Resource limits**: hard size cap (`ZENCODED_MAX_DOWNLOAD_BYTES`, checked against
  `Content-Length` *and* enforced while streaming) and request timeouts.
- **Isolation**: bytes stream to a per-job subdirectory under `temp/`, are **never
  executed** server-side, and the directory is removed when the job finishes.
- **Residual risk**: a small DNS-rebinding TOCTOU window remains between our resolution
  and the connection's own resolution. For this single-operator service the per-hop
  validation is the accepted mitigation; pin to validated IPs (or an egress proxy /
  network policy that blocks RFC-1918 + link-local) if the threat model tightens.

## C. Publishing to GitHub (least privilege) — `publisher.py`

- **Deploy key scoped to this repo only.** Publishing uses an SSH deploy key with write
  access to *only* this repository, so a server compromise cannot reach other repos or
  the wider account. Provide it via `ZENCODED_DEPLOY_KEY_PATH` (file mode `600`,
  ideally a mounted secret).
- **No key sprawl / no host TOFU.** Git invokes SSH through `GIT_SSH_COMMAND` with
  `IdentitiesOnly=yes` (ignores agent/other keys) and `StrictHostKeyChecking=yes`
  against a pinned `ZENCODED_KNOWN_HOSTS_PATH`.
- **Confined writes.** Generated files are written only under `data/`; `_resolve_within`
  rejects any path that would escape it (traversal defense).
- **Traceable, non-destructive commits.** Commit messages record the source URL, the
  acting operator, and the job id. The flow is `add → commit → pull --rebase → push`;
  pushes are never forced. Consider GitHub branch protection on the target branch.

### Large files: GitHub Release assets — `releaser.py`

Files over GitHub's hard 100 MiB per-file push limit can't be committed, so in
`release`/`auto` publish mode the encoded script is uploaded as a Release asset instead.

- **Separate credential, still least-privilege.** The Releases REST API cannot be called
  with the SSH deploy key, so it uses `ZENCODED_GITHUB_TOKEN` — a **fine-grained PAT (or
  GitHub App token) scoped to this single repo** with only `Contents: write`. Keep it in
  a secret store; it is never committed (see `.gitignore`). This is a distinct, narrower
  credential from any account-wide token.
- **Idempotent overwrite.** Uploading reuses a single rolling release tag; an existing
  same-named asset is deleted before re-upload (assets are immutable), matching the repo's
  overwrite semantics.
- **Portability preserved.** The asset's `browser_download_url` serves the raw bytes, so
  the self-extractor remains fetchable as plain text (`curl -O <url>`).

## Operational notes

- All secrets come from the environment / a secret manager — never commit `.env` or key
  files (see `.gitignore`).
- The generated self-extractors verify SHA-256 and refuse to overwrite an existing file
  without `--force`. Running any self-extractor is still a trust decision for whoever
  executes it downstream.
