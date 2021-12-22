"""logger.py.

Logging utilities for web_radio.
"""
import logging


logger = logging.getLogger('pysdrweb')


def get_child_logger(name):
    """Get the logger for the given scope."""
    return logger.getChild(name)
