"""context.py.

Implementation of the 'FmServerContext' and associated helpers.
"""
import asyncio
from tornado import web
# Local Imports
from pysdrweb.util.logger import logger
from pysdrweb.util.auth import parse_auth_manager_from_options
from pysdrweb.fmserver.hls_streaming import HLSManager
from pysdrweb.fmserver.driver import RtlFmExecDriver


class FmServerContext(object):
    """Context for the FM Server.

    This class stores various pieces for the server, all in one place. In
    particular, this stores:
     - Driver (PCM data generator)
     - Auth Manager (if applicable)
     - HLS Manager (if configured)
     - Default/starting frequency
    """

    def __init__(self, driver, auth_manager, hls_manager=None, default_frequency=None):
        self._driver = driver
        self._auth_manager = auth_manager
        if default_frequency:
            self._default_frequency = default_frequency
        else:
            # Pick some random frequency. This should be passed, usually.
            self._default_frequency = '107.3M'

        self._hls_manager = hls_manager

    @property
    def driver(self):
        """Return the driver for this context."""
        return self._driver

    @property
    def auth_manager(self):
        """Return the AuthManager for this context."""
        return self._auth_manager

    @property
    def hls_manager(self):
        """Return the HLSManager for this context (if applicable).

        If HLS is not configured, this returns None.
        """
        return self._hls_manager

    async def start(self):
        """Start the driver (and the HLSManager, if applicable)."""
        # Start the driver.
        logger.info('Starting on frequency: %s', self._default_frequency)
        await self.driver.start(self._default_frequency)
        if self.hls_manager:
            logger.info("Generating HLS audio chunks.")
            fut = asyncio.create_task(self.hls_manager.run())
            self.driver.add_awaitable(fut)

    async def stop(self):
        self.driver.stop()
        await self.driver.wait()

    async def change_frequency(self, new_frequency):
        """Change the (FM) frequency for the server.

        NOTE: This also handles other things that might need to be updated,
        such as the HLS manager and so forth.
        """
        await self.driver.change_frequency(new_frequency)

        # NOTE: The HLS manager also needs to be restarted.
        if self.hls_manager:
            fut = asyncio.create_task(self.hls_manager.run())
            self.driver.add_awaitable(fut)


def parse_option_dict(option_dict):
    """Parse the given dict of options and return an FMServerContext."""
    auth_manager = parse_auth_manager_from_options(option_dict)

    # TODO -- Handle more driver types.
    driver = RtlFmExecDriver.from_config(option_dict.get('driver', {}))

    # Parse HLS options.
    hls_option_dict = option_dict.get('hls', dict())
    if hls_option_dict and hls_option_dict.get('enabled', False):
        # Parse the applicable fields.
        chunk_count = hls_option_dict.get('chunk_count', 6)
        chunk_secs = hls_option_dict.get('seconds_per_chunk', 10)
        fmt = hls_option_dict.get('format')
        hls_manager = HLSManager(
            driver, count=chunk_count, secs_per_chunk=chunk_secs, fmt=fmt)
    else:
        hls_manager = None

    # Return the context with all of these parsed options.
    return FmServerContext(
        driver, auth_manager, hls_manager,
        option_dict.get('default_frequency')
    )
