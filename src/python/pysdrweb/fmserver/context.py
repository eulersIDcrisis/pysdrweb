"""context.py.

Implementation of the 'FmServerContext' and associated helpers.
"""

from typing import Optional, Any
import asyncio
from dataclasses import dataclass, asdict, field

# Local Imports
from pysdrweb.util.logger import logger
from pysdrweb.util.auth import parse_auth_manager_from_options, BaseAuthManager
from pysdrweb.fmserver.hls_streaming import HLSManager, HLSConfig
from pysdrweb.drivers import RtlFmExecDriver, RtlFmConfig, AbstractPCMDriver


@dataclass
class FmServerConfig:
    """Configuration for the FM server."""

    ports: list[int| str] = field(default_factory=lambda: [9000])
    """List of ports (and UNIX socket files) to listen on."""

    driver: RtlFmConfig = field(default_factory=RtlFmConfig)
    """Configuration for the driver to generate PCM data."""

    hls: HLSConfig = field(default_factory=HLSConfig)
    """Configuration for HLS streaming. If None, it isn't enabled."""

    def to_dict(self) -> dict[str, Any]:
        """Export this configuration to a (JSON-serializable) dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, config_dict):
        """Import a dictionary into this config."""
        ports = config_dict.get("ports", [9000])
        driver = RtlFmConfig.from_dict(config_dict.get("driver", {}))
        hls = HLSConfig.from_dict(config_dict.get("hls", {}))
        return cls(ports=ports, driver=driver, hls=hls)


class FmServerContext:
    """Context for the FM Server.

    This class stores various pieces for the server, all in one place. In
    particular, this stores:
     - Driver (PCM data generator)
     - Auth Manager (if applicable)
     - HLS Manager (if configured)
     - Default/starting frequency
    """

    def __init__(self, config: FmServerConfig):
        self._driver = RtlFmExecDriver(config.driver)
        self._auth_manager = None
        self._default_frequency = config.driver.default_frequency
        if not self._default_frequency:
            # Pick some random frequency. This should be passed, usually.
            self._default_frequency = "107.3M"

        if config.hls.enabled:
            self._hls_manager = HLSManager(self._driver, config.hls)
        else:
            self._hls_manager = None

    @property
    def driver(self) -> AbstractPCMDriver:
        """Return the driver for this context."""
        return self._driver

    @property
    def auth_manager(self) -> BaseAuthManager:
        """Return the AuthManager for this context."""
        return self._auth_manager

    @property
    def hls_manager(self) -> Optional[HLSManager]:
        """Return the HLSManager for this context (if applicable).

        If HLS is not configured, this returns None.
        """
        return self._hls_manager

    async def start(self):
        """Start the driver (and the HLSManager, if applicable)."""
        # Start the driver.
        logger.info("Starting on frequency: %s", self._default_frequency)
        await self.driver.start(self._default_frequency)
        if self.hls_manager:
            logger.info("Generating HLS audio chunks.")
            fut = asyncio.create_task(self.hls_manager.run())
            self.driver.add_awaitable(fut)

    async def stop(self):
        """Stop the driver and wait for it to cleanup."""
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
