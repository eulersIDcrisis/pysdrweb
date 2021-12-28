"""context.py.

Implementation of the 'FmServerContext' and associated helpers.
"""
import asyncio
from tornado import web
# Local Imports
from pysdrweb.util.logger import logger
from pysdrweb.fmserver import hls_streaming


class FmServerContext(object):

    def __init__(self, driver, auth_manager, default_frequency=None):
        self._driver = driver
        self._auth_manager = auth_manager
        if default_frequency:
            self._default_frequency = default_frequency
        else:
            # Pick some random frequency. This should be passed, usually.
            self._default_frequency = '107.3M'

        self._hls_manager = hls_streaming.HLSManager(driver)

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
        """Return the HLSManager for this context (if applicable.)

        If HLS is not configured, this returns None.
        """
        return self._hls_manager

    async def start(self):
        # Start the driver.
        logger.info('Starting on frequency: %s', self._default_frequency)
        await self.driver.start(self._default_frequency)
        if self.hls_manager:
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
