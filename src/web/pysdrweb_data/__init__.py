"""pysdrweb.data Module.

Module to help with loading (static) resources bundled with pysdrweb.
"""

from typing import BinaryIO
from importlib.resources import files


def get_data_file_stream(path: str) -> BinaryIO:
    """Return a file-like stream to the given path.

    This is designed to load paths internal to the module that should be
    bundled with the installation.
    """
    return files(__name__).joinpath(path).open("rb")
