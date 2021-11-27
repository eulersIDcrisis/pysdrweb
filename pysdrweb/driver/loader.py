"""loader.py.

Module that loads the available drivers for the server.
"""
from pysdrweb.driver.icecast import IcecastRtlFMDriver
from pysdrweb.driver.rtl_native import RtlFMNativeDriver


def get_driver_mapping():
    """Return the currently available drivers in the system.

    The result is a mapping of the driver name (as a string) to the driver
    type (as a python class). The driver type/class should accept a dict
    of configuration options and return the driver.

    Returns
    -------
    dict of: <name> --> Driver factory method/function/constructor.
    """
    return {
        # NOTE: For the icecast driver, use the 'from_config' factory method
        # which will try to find the missing executables if not already set.
        'icecast': IcecastRtlFMDriver.from_config,
        # NOTE: For the native driver, use the 'from_config' factory method,
        # which will try to find the 'rtl_fm' path, if not already set.
        'native': RtlFMNativeDriver.from_config,
    }
