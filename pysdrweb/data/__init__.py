"""pysdrweb.data Module.

Module to help with loading (static) resources bundled with pysdrweb.
"""
import importlib.resources as internal


def get_data_file_stream(path):
    """Return a file-like stream to the given path.

    This is designed to load paths internal to the module that should be
    bundled with the installation.
    """
    return internal.open_binary(__name__, path)
