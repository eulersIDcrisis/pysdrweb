"""logger.py.

Logging utilities for web_radio.
"""

import logging


logger = logging.getLogger("pysdrweb")


def get_child_logger(name) -> logging.Logger:
    """Get the logger for the given scope."""
    return logger.getChild(name)


auth_logger = get_child_logger("auth")
"""Logger that logs information about authentication failures."""
