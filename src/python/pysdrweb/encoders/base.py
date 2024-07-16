"""base.py.

Module with shared files between the different encoder types.
"""

from typing import Optional, Callable, Awaitable
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pysdrweb.util.misc import PCMBufferAddress
from pysdrweb.drivers import AbstractRtlDriver


class UnsupportedFormatError(Exception):
    """Exception indicating an unsupported format."""


class BaseEncoder(ABC):
    """Base encoder class with the different properties"""

    def __init__(self, driver: AbstractRtlDriver) -> None:
        super().__init__()
        self._driver = driver

    @property
    def driver(self) -> AbstractRtlDriver:
        return self._driver

    @classmethod
    @abstractmethod
    def get_supported_formats(cls) -> Sequence[str]:
        """Return the formats supported by this encoder."""

    @abstractmethod
    async def encode(
        self,
        stream,
        format_type: str,
        timeout: Optional[float] = None,
        async_flush: Callable[[], Awaitable[None]] = None,
        start_address=None,
    ) -> PCMBufferAddress:
        """Encode the output from the driver into the given stream."""
