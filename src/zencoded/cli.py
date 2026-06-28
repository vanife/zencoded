"""Command-line interface for the zencoded encoder.

    zencoded encode PATH [--compress auto|always|never] [--format py|data] [-o DIR]
    zencoded encode-url URL [--compress ...] [--format py|data] [-o DIR]
    zencoded extract FILE.txt [-o DIR] [--force]

``--format py`` (default) writes a self-extracting ``.py``; ``--format data`` writes a
``<name>.txt`` data file (JSON header + base64) reconstructed with ``extract.py`` or the
``extract`` subcommand. By default output goes to the configured ``data/`` directory.
``encode-url`` reuses the SSRF-safe downloader.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import encoder
from .config import get_settings
from .decoder import DecodeError, extract_datafile
from .downloader import DownloadError, download


def _add_encode_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--compress",
        choices=("auto", "always", "never"),
        default=None,
        help="compression mode (default: configured DEFAULT_COMPRESS, 'never')",
    )
    p.add_argument(
        "--format",
        choices=("py", "data"),
        default="py",
        help="output format: self-extracting .py (default) or .txt data file",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="directory to write the generated file into (default: data/)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zencoded", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encode", help="encode a local file")
    enc.add_argument("path", help="path to the file to encode")
    _add_encode_opts(enc)

    url = sub.add_parser("encode-url", help="download a URL and encode it")
    url.add_argument("url", help="http(s) URL to download and encode")
    _add_encode_opts(url)

    ext = sub.add_parser("extract", help="reconstruct a file from a .txt data file")
    ext.add_argument("datafile", help="path to the data file")
    ext.add_argument("-o", "--output-dir", default=".", help="output directory")
    ext.add_argument("-f", "--force", action="store_true", help="overwrite existing file")

    return parser


def _write_encoded(output_dir: Path, fmt: str, result) -> Path:
    """Write either a .py self-extractor or a .txt data file; return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "data":
        out = output_dir / encoder.datafile_filename(result.filename)
        out.write_text(result.content, encoding="utf-8")
    else:
        out = output_dir / encoder.script_filename(result.filename)
        out.write_text(result.script, encoding="utf-8")
    return out


def _encode(fmt: str, **kwargs):
    """Dispatch to the right encoder based on output format."""
    if fmt == "data":
        return encoder.encode_file_to_datafile(**kwargs)
    return encoder.encode_file(**kwargs)


def _do_extract(args) -> int:
    try:
        out = extract_datafile(args.datafile, args.output_dir, force=args.force)
    except (DecodeError, OSError) as exc:
        print(f"extract error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()

    if args.command == "extract":
        return _do_extract(args)

    compress = args.compress or settings.default_compress
    output_dir = Path(args.output_dir) if args.output_dir else settings.data_dir

    if args.command == "encode":
        src = Path(args.path)
        if not src.is_file():
            print(f"error: not a file: {src}", file=sys.stderr)
            return 2
        result = _encode(args.format, path=src, compress=compress, source=str(src))
        out = _write_encoded(output_dir, args.format, result)

    elif args.command == "encode-url":
        try:
            result, out = asyncio.run(
                _encode_url(settings, args.url, compress, args.format, output_dir)
            )
        except DownloadError as exc:
            print(f"download error: {exc}", file=sys.stderr)
            return 1
    else:  # pragma: no cover - argparse enforces a valid command
        return 2

    print(
        f"wrote {out} "
        f"(sha256={result.sha256[:12]}…, "
        f"{'gzip+base64' if result.compressed else 'base64'}, "
        f"{result.original_size} bytes)"
    )
    return 0


async def _encode_url(settings, url, compress, fmt, output_dir):
    work_dir = settings.temp_dir / "cli"
    result = await download(
        url,
        work_dir,
        max_bytes=settings.max_download_bytes,
        timeout=settings.download_timeout,
        max_redirects=settings.max_redirects,
    )
    encoded = _encode(fmt, path=result.path, compress=compress, source=result.final_url)
    out = _write_encoded(output_dir, fmt, encoded)
    result.path.unlink(missing_ok=True)
    return encoded, out


if __name__ == "__main__":
    raise SystemExit(main())
