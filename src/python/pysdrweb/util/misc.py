"""misc.py.

Miscellaneous Helpers.
"""

# Typing Imports
from collections.abc import Callable
from typing import Any, Optional

# Standard Imports
import os
import shlex
import subprocess
from collections import namedtuple

# Import to extract the version information.
try:
    from importlib.metadata import version

    def get_version() -> str:
        """Return the version of pysdrweb."""
        try:
            return version("wheel")
        except Exception:
            return "local"

except ImportError:
    import pkg_resources

    def get_version() -> str:
        """Return the version of pysdrweb."""
        try:
            return pkg_resources.get_distribution("pysdrweb").version
        except Exception:
            return "local"


import asyncio


async def read_lines_from_stream(
    read_stream: asyncio.StreamReader,
    callback: Callable[[bytes], Any],
    encoding: str = "utf-8",
):
    """Parse the given stream, and invoke 'callback' for each line parsed.

    This continues parsing from the stream until the stream is closed and
    will decode the line into a string ('utf-8' by default) before invoking
    the callback. (Setting 'encoding=None' will pass the raw bytes to the
    callback if the caller does not want to decode the line.)
    """
    while not read_stream.at_eof():
        data = await read_stream.readline()
        if not data:
            return
        if encoding:
            data = data.decode(encoding)
        callback(data)


async def close_pipe_on_exit(proc: asyncio.subprocess.Process, fd: int):
    """Helper that will close the given fd (pipe) when 'proc' finishes.

    Useful when chaining processes because sometimes the write side of the
    pipe needs to be explicitly closed for a clean shutdown.
    """
    await proc.wait()
    os.close(fd)


def find_executable(cmd: str) -> Optional[str]:
    """Find the given executable from the current environment.

    NOTE: This effectively runs: `which ${cmd}` and uses the resulting
    path (if any).
    """
    cmd = shlex.join(["which", shlex.quote(cmd)])
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True, check=False)
    if proc.stdout:
        return proc.stdout.decode("utf-8").strip()
    return None


#
# PCM Buffer Utility Classes
#
PCMBufferItem = namedtuple("PCMBufferItem", ["seq_index", "data"])
"""Tuple that stores a "PCM" chunk and the index it pertains to.

The elements of the tuple are:
 - 'index': Some monotonically increasing value that flags where in the
        stream this particular chunk belongs.
 - 'data': The buffer of data
"""


PCMBufferAddress = namedtuple("PCMBufferAddress", ["seq_index", "index"])
"""Tuple that stores an address for a "PCM" chunk.

This tuple addresses a byte position for some sequence of 'PCMBufferItem'
objects as defined above, with the following pieces:
 - 'seq_index': Index in the sequence of PCMBufferItems. This addresses
        which PCMBufferItem to reference.
 - 'index': Index inside the PCMBufferItem. This addresses the specific
        byte inside the buffer of an individual PCMBufferItem.

As a namedtuple, this inherits the appropriate comparisions automatically.
"""


MIN_PCM_ADDRESS = PCMBufferAddress(-1, -1)
"""PCMBufferAddress less than any other."""
