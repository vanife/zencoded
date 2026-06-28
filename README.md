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

## Two output formats

Each file can be encoded as either:

- **Self-extracting `.py`** (default) — one standalone, runnable script (above).
- **Data file `<name>.txt`** (`--format data`) — a plain-text file with a human-readable
  JSON header, a `---` separator, then the base64 body. It is *not* Python, so it is never
  compiled — reconstruct it with the standalone, stdlib-only [`extract.py`](./extract.py):

  ```bash
  python extract.py data/<name>.txt            # writes the original file here
  python extract.py data/<name>.txt -o out/    # into another directory
  python extract.py data/<name>.txt --force    # overwrite an existing target
  python extract.py data/<name>.txt --stdout   # raw bytes to stdout
  ```

  The data file looks like:

  ```
  {
      "compression": "gzip",
      "encoding": "base64",
      "filename": "tool.zip",
      "sha256": "9f86d0…",
      "size": 524288000,
      "zencoded": 1
  }
  ---
  <base64 …>
  ```

  Prefer this for **large files**: extraction streams the body with flat memory and no
  per-run compile cost (a multi-hundred-MB `.py` is slow and memory-hungry to compile).

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
uv run zencoded encode ./somefile.bin                 # -> data/somefile.bin.py
uv run zencoded encode ./somefile.bin --format data   # -> data/somefile.bin.txt
uv run zencoded encode-url https://…                  # download + encode a URL
uv run zencoded extract ./data/somefile.bin.txt -o .  # reconstruct a data file
uv run uvicorn zencoded.web.app:app --reload   # run the web service (needs .env)
```

See [`.env.example`](./.env.example) for required configuration and
[`docs/SECURITY.md`](./docs/SECURITY.md) for the security model.
