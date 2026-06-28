# zencoded

A repository of various tools and installations encoded as **self-extracting base64
Python scripts**, so that binaries can be passed over the internet as plain text files.

Each script under [`data/`](./data) is a standalone Python program. Running it
reconstructs the original file (original name preserved) and verifies its SHA-256
checksum:

```bash
python data/<name>.py            # writes the original file into the current directory
python data/<name>.py --force    # overwrite if the target already exists
python data/<name>.py -o out/    # write into a different directory
```

The scripts depend only on the Python standard library (Python 3.8+ to *run* an
already-generated extractor).

## Components

- **Core encoder** (`src/zencoded/encoder.py`, `template.py`) — turns any file into a
  self-extractor script. Compression (`auto`/`always`/`never`) is optional, since many
  payloads (`.zip`, `.exe`) are already compressed.
- **CLI** (`zencoded …`) — encode a local file or a URL from the terminal.
- **Web service** (`src/zencoded/web/`) — a FastAPI app where an authenticated operator
  submits a URL; the server downloads it, encodes it, and commits + pushes the script to
  this repository.

## Development

This is a [uv](https://docs.astral.sh/uv/)-managed project (Python ≥ 3.13).

```bash
uv sync                                   # install deps
uv run pytest                             # run tests
uv run zencoded encode ./somefile.bin     # encode a local file -> data/somefile.bin.py
uv run zencoded encode-url https://…      # download + encode a URL
uv run uvicorn zencoded.web.app:app --reload   # run the web service (needs .env)
```

See [`.env.example`](./.env.example) for required configuration and
[`docs/SECURITY.md`](./docs/SECURITY.md) for the security model.
