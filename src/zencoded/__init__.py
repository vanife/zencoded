"""zencoded — encode files as self-extracting base64 Python scripts."""

from .encoder import (
    CompressMode,
    EncodeResult,
    encode_bytes,
    encode_file,
    script_filename,
)

__all__ = [
    "CompressMode",
    "EncodeResult",
    "encode_bytes",
    "encode_file",
    "script_filename",
]

__version__ = "0.1.0"
