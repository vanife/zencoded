"""zencoded — encode files as self-extracting base64 Python scripts."""

from .encoder import (
    CompressMode,
    DatafileResult,
    EncodeResult,
    datafile_filename,
    encode_bytes,
    encode_bytes_to_datafile,
    encode_file,
    encode_file_to_datafile,
    script_filename,
)

__all__ = [
    "CompressMode",
    "DatafileResult",
    "EncodeResult",
    "datafile_filename",
    "encode_bytes",
    "encode_bytes_to_datafile",
    "encode_file",
    "encode_file_to_datafile",
    "script_filename",
]

__version__ = "0.1.0"
