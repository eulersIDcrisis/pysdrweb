"""logger.py.

Logging utilities for web_radio.
"""
import logging


logger = logging.getLogger('webradio')

def get_child_logger(name):
    return logger.getChild(name)
