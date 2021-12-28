"""misc.py.

Miscellaneous Helpers.
"""
import os
import shlex
import subprocess
from collections import namedtuple
# Import to extract the version information.
import pkg_resources


_RANGE_HEADER_PREFIX = b'bytes='


def get_version():
    """Return the version of pysdrweb."""
    try:
        return pkg_resources.get_distribution('pysdrweb').version
    except Exception:
        return 'local'


async def read_lines_from_stream(read_stream, callback, encoding='utf-8'):
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


async def close_pipe_on_exit(proc, fd):
    """Helper that will close the given fd (pipe) when 'proc' finishes.

    Useful when chaining processes because sometimes the write side of the
    pipe needs to be explicitly closed for a clean shutdown.
    """
    await proc.wait()
    os.close(fd)


def find_executable(cmd):
    """Find the given executable from the current environment.

    NOTE: This effectively runs: `which ${cmd}` and uses the resulting
    path (if any).
    """
    cmd = shlex.join(['which', shlex.quote(cmd)])
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
    if proc.stdout:
        return proc.stdout.decode('utf-8').strip()
    return None


#
# PCM Buffer Utility Classes
#
PCMBufferItem = namedtuple('PCMBufferItem', ['seq_index', 'data'])
"""Tuple that stores a "PCM" chunk and the index it pertains to.

The elements of the tuple are:
 - 'index': Some monotonically increasing value that flags where in the
        stream this particular chunk belongs.
 - 'data': The buffer of data
"""


PCMBufferAddress = namedtuple('PCMBufferAddress', ['seq_index', 'index'])
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


#
# HTTP Header Parsing Utilities
#
def parse_range_header(range_header):
    """Parse the 'Range' header and return a tuple of: (start, end).

    NOTE: This currently only supports 1 tuple; multiple ranges in the
    same header are not supported for now.

    Returns
    -------
    tuple of: (int or None, int or None)
        Tuple with the start index and end index. 'None' implies the end of
        the range.
    """
    try:
        if not range_header:
            return None, None
        # Ignore these requests. The HTTP spec implies that an invalid 'Range'
        # header should just be ignored, so we'll just return instead of
        # raising an exception for now.
        if not range_header.startswith(_RANGE_HEADER_PREFIX):
            return None, None

        ranges = range_header[len(_RANGE_HEADER_PREFIX):].split(',')
        if not ranges:
            return None, None

        # TODO -- We could (hypothetically) parse each range in 'ranges', but
        # for now, we'll only parse the first one.
        parsed_range = ranges[0]

        start_str, end_str = parsed_range.split('-')

        start = int(start_str) if start_str else None
        end = int(end_str) if end_str else None
        return start, end
    except Exception:
        return None, None
