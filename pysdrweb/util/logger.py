"""logger.py.

Logging utilities for web_radio.
"""
import logging


logger = logging.getLogger('webradio')

def get_child_logger(name):
    return logger.getChild(name)


def get_version():
    """Return the version of pysdrweb."""
    from pysdrweb.__about__ import __version__
    return __version__
