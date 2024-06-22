"""encoders.py.

Main module to import encoders.

This will attempt to import the various modules below and simply exclude them
when they fail to import. This permits a more configurable set of encoders to
use as various libraries are available.
"""

from pysdrweb.encoders.base import BaseEncoder, UnsupportedFormatError
from pysdrweb.encoders.pynative import StandardLibraryEncoder


_ENCODER_REGISTRY: dict[str, BaseEncoder] = {}


# Native library should always be okay.
for format_type in StandardLibraryEncoder.get_supported_formats():
    _ENCODER_REGISTRY[format_type] = StandardLibraryEncoder

try:
    from pysdrweb.encoders.lame_mp3 import Mp3Encoder

    # Add the Mp3Encoder.
    for format_type in Mp3Encoder.get_supported_formats():
        _ENCODER_REGISTRY[format_type] = Mp3Encoder
except ImportError:
    raise

try:
    from pysdrweb.encoders.soundfile_util import SoundfileEncoder

    for format_type in SoundfileEncoder.get_supported_formats():
        _ENCODER_REGISTRY[format_type] = SoundfileEncoder

except ImportError:
    raise


_FORMAT_REGISTRY = {
    "WAV": "audio/wav",
    "AIFF": "audio/aiff",
    "AIFC": "audio/aiff",
    "FLAC": "audio/flac",
    "OGG": "audio/ogg",
    "MP3": "audio/mp3",
}

IS_MP3_AVAILABLE = bool("MP3" in _FORMAT_REGISTRY)
"""Return whether an MP3-compatible encoder is available.

This is useful because some formats (HLS in particular) can work better when
MP3 is explicitly available.
"""

IS_FLAC_AVAILABLE = bool("FLAC" in _FORMAT_REGISTRY)
"""Return whether an MP3-compatible encoder is available.

This is useful because some formats (HLS in particular) can work better when
MP3 is explicitly available.
"""


def get_encoder_for_format_type(driver, format_type: str, /, **kwds) -> BaseEncoder:
    """Return the encoder for the given format type."""
    enc = _ENCODER_REGISTRY.get(format_type)
    if enc is None:
        raise UnsupportedFormatError(f"Unsupported format type: {format_type}")
    # For now, just return the first entry.
    return enc(driver, **kwds)


def get_mime_type_for_format(fmt):
    """Return the MIME type for the given format."""
    return _FORMAT_REGISTRY.get(fmt.upper(), "application/octet-stream")
