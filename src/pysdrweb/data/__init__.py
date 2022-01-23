"""pysdrweb.data Module.

Module to help with loading (static) resources bundled with pysdrweb.
"""
import pkg_resources


def get_data_file_stream(path):
    """Return a file-like stream to the given path.

    This is designed to load paths internal to the module that should be
    bundled with the installation.
    """
    return pkg_resources.resource_stream(__name__, path)


def list_data_files(path='.'):
    """Return a list of the files available at the given path."""
    return pkg_resources.resource_listdir(__name__, path)
