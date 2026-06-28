"""Command-line interface for the zencoded encoder.

    zencoded encode PATH [--compress auto|always|never] [-o DIR]
    zencoded encode-url URL [--compress ...] [-o DIR]

By default generated scripts are written to the configured ``data/`` directory.
``encode-url`` reuses the SSRF-safe downloader.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import encoder
from .config import get_settings
from .downloader import DownloadError, download


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--compress",
        choices=("auto", "always", "never"),
        default=None,
        help="compression mode (default: configured DEFAULT_COMPRESS, 'never')",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="directory to write the generated script into (default: data/)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zencoded", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encode", help="encode a local file")
    enc.add_argument("path", help="path to the file to encode")
    _add_common(enc)

    url = sub.add_parser("encode-url", help="download a URL and encode it")
    url.add_argument("url", help="http(s) URL to download and encode")
    _add_common(url)

    return parser


def _write_script(output_dir: Path, encoded: encoder.EncodeResult) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / encoder.script_filename(encoded.filename)
    out.write_text(encoded.script, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    compress = args.compress or settings.default_compress
    output_dir = Path(args.output_dir) if args.output_dir else settings.data_dir

    if args.command == "encode":
        src = Path(args.path)
        if not src.is_file():
            print(f"error: not a file: {src}", file=sys.stderr)
            return 2
        encoded = encoder.encode_file(src, compress=compress, source=str(src))
        out = _write_script(output_dir, encoded)

    elif args.command == "encode-url":
        try:
            encoded, out = asyncio.run(_encode_url(settings, args.url, compress, output_dir))
        except DownloadError as exc:
            print(f"download error: {exc}", file=sys.stderr)
            return 1
    else:  # pragma: no cover - argparse enforces a valid command
        return 2

    print(
        f"wrote {out} "
        f"(sha256={encoded.sha256[:12]}…, "
        f"{'gzip+base64' if encoded.compressed else 'base64'}, "
        f"{encoded.original_size} bytes)"
    )
    return 0


async def _encode_url(settings, url, compress, output_dir):
    work_dir = settings.temp_dir / "cli"
    result = await download(
        url,
        work_dir,
        max_bytes=settings.max_download_bytes,
        timeout=settings.download_timeout,
        max_redirects=settings.max_redirects,
    )
    encoded = encoder.encode_file(result.path, compress=compress, source=result.final_url)
    out = _write_script(output_dir, encoded)
    result.path.unlink(missing_ok=True)
    return encoded, out


if __name__ == "__main__":
    raise SystemExit(main())
