"""hls_streaming.py.

Module with objects to handle HLS streaming.
"""

from typing import Optional, BinaryIO
import io
import asyncio
from collections import OrderedDict
from dataclasses import dataclass

# Third-party imports.
from tornado import web

# Local imports.
from pysdrweb.util import misc
from pysdrweb.util.auth import authenticated
from pysdrweb.util.logger import get_child_logger
from pysdrweb.drivers import AbstractPCMDriver
from pysdrweb.encoders import (
    get_encoder_for_format_type,
    get_mime_type_for_format,
    IS_FLAC_AVAILABLE,
    IS_MP3_AVAILABLE,
)


logger = get_child_logger("hls")


@dataclass
class HLSConfig:
    """Configuration for HLS settings."""
    enabled: bool = False
    """Store whether HLS is enabled."""

    chunk_count: int = 6
    """Number of chunks to have stored in the manager."""

    secs_per_chunk: float = 10
    """Duration (in seconds) of each chunk."""

    fmt: Optional[str] = None
    """Format of each chunk."""

    start_index: int = 1
    """First index to return.

    The files are named sequentially and this field rarely needs to be
    touched.
    """


class HLSManager:
    """Manager that encodes an incoming (PCM) stream into an HLS format."""

    @classmethod
    def from_config(cls, config_dict) -> HLSConfig:
        config = HLSConfig()
        config.enabled = bool(config_dict.get("enabled", False))
        if "chunk_count" in config_dict:
            config.chunk_count = int(config_dict["chunk_count"])
        if "seconds_per_chunk" in config_dict:
            config.secs_per_chunk = float(config_dict["seconds_per_chunk"])
        if "format" in config_dict:
            config.fmt = config_dict["format"]
        return config

    def __init__(
        self,
        driver: AbstractPCMDriver,
        config: HLSConfig,
    ):
        """Create the HLSManager that writes audio files for HLS streaming.

        HLS Streaming involves taking a continuous stream of media, then
        splitting it into smaller 'static' file chunks so that it can be
        served more efficiently.

        This manager handles the creation (and deletion) of these files as
        well as various metadata to manage this so that the appropriate data
        can be served by the backend to support this flow. This stream should
        not otherwise interfere with the existing PCM driver any more than any
        other consumer of the PCM data stream.

        The different options configure the format of the static files to be
        generated, along with the number and duration of these files.
        """
        if config.fmt is None:
            # Use MP3 first if it is available.
            if IS_MP3_AVAILABLE:
                config.fmt = "MP3"
            # Use FLAC by default (if supported), since this should be
            # supported by most clients. Fallback to WAV, which also should be
            # supported, if we cannot encode to FLAC.
            elif IS_FLAC_AVAILABLE:
                config.fmt = "FLAC"
            else:
                config.fmt = "WAV"
        self._config = config
        if self._config.chunk_count < 6:
            # Warn if creating the HLS manager with less than 6 chunks, as is
            # recommended by Apple and others.
            logger.warning(
                "HLS should have more than 6 chunks. Configured with: %s ",
                self._chunk_count,
            )
        self._next_idx = config.start_index
        # Store a mapping of: <index> -> buffer or path
        self._file_mapping: dict[int, BinaryIO] = OrderedDict()
        # Store the driver.
        self._driver = driver
        self._stop_requested = asyncio.Event()
        self._done_event = asyncio.Event()

    @property
    def driver(self) -> AbstractPCMDriver:
        """Return the underlying driver for this HLS manager."""
        return self._driver

    @property
    def chunk_count(self) -> int:
        """Return the configured number of chunks for the HLS chunking."""
        return self._config.chunk_count

    @property
    def secs_per_chunk(self) -> float:
        """Return the duration (in seconds) for each audio chunk."""
        return self._config.secs_per_chunk

    @property
    def fmt(self) -> str:
        """Return the format for each audio chunk."""
        return self._config.fmt

    @property
    def mime_type(self) -> str:
        """Return the MIME type for the streaming audio file chunks."""
        return get_mime_type_for_format(self.fmt)

    @property
    def ext(self) -> str:
        """Extension type when streaming audio chunks."""
        return self._config.fmt.lower()

    def is_running(self) -> bool:
        return not self._done_event.is_set()

    def get_available_chunks(self, basename: str = "audio") -> list[str]:
        """Return a list of the available chunks."""
        return [f"{basename}{key}.{self.ext}" for key in self._file_mapping.keys()]

    def get_data(self, index: int) -> Optional[BinaryIO]:
        return self._file_mapping.get(index)

    async def run(self):
        """Run the HLS stream.

        This needs to encode the data dynamically.
        """
        # Iterate over the data, writing out a new file.
        start_addr = misc.MIN_PCM_ADDRESS
        while not self.driver.stop_event.is_set():
            file_obj = io.BytesIO()
            encoder = get_encoder_for_format_type(self.driver, self.fmt)
            start_addr = await encoder.encode(
                file_obj,
                self._fmt,
                timeout=self._secs_per_chunk,
                start_address=start_addr,
            )
            self._file_mapping[self._next_idx] = file_obj
            while len(self._file_mapping) > self.chunk_count:
                # Remove the oldest item.
                index, buff = self._file_mapping.popitem(last=False)
                logger.debug("Removing index: %s", index)
                buff.close()
            logger.debug("Wrote index: %s", self._next_idx)
            self._next_idx += 1

        self._file_mapping.clear()
        self._next_idx = 1
        self._done_event.set()


#
# Handlers for HLS
#
class HlsRequestHandler(web.RequestHandler):

    def initialize(self, context=None):
        """Initialize the handler with the current context."""
        self._context = context

    def get_driver(self) -> AbstractPCMDriver:
        """Get the FM driver for the handler."""
        return self.get_context().driver

    def get_context(self):
        """Get the current FMServerContext for the handler."""
        return self._context

    def send_status(self, code, message):
        """Helper to send a JSON message for the given status."""
        self.set_status(code)
        self.write({"status": code, "message": message})


class HlsPlaylistHandler(HlsRequestHandler):

    def compute_etag(self):
        return None

    @authenticated(readonly=True)
    async def get(self):
        try:
            manager = self.get_context()._hls_manager

            self.set_header("Content-Type", "application/x-mpegurl")
            # Disable caching for this handler?
            self.set_header("Cache-Control", "no-cache")

            # Write out the file content for the HLS playlist file.
            self.write("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-VERSION:3\n")
            secs = manager.secs_per_chunk
            first_written = False
            # Write out all of the files.
            for idx in manager._file_mapping.keys():
                if not first_written:
                    self.write(f"#EXT-MEDIA-SEQUENCE:{idx}\n")
                    first_written = True
                self.write(f"#EXTINF:{secs:.2f},\n")
                self.write(f"audio{idx}.{manager.ext}\n")
        except Exception:
            logger.exception("Error generating HLS Playlist (m3u8) file!")
            self.send_status(500, "Internal Server Error")
            return


class HlsFileHandler(HlsRequestHandler):

    @authenticated(readonly=True)
    async def get(self, num, ext):
        try:
            index = int(num)
            manager = self.get_context()._hls_manager

            data = manager.get_data(index)
            if not data:
                self.send_status(404, "Chunk not found.")
                return

            # Set the header type.
            self.set_header("Content-Type", manager.mime_type)
            self.write(data.getvalue())
        except Exception:
            logger.exception("Error sending chunk at index: %s", num)
            self.send_status(404, "Chunk not found.")


def get_hls_routes(context, prefix=""):
    context_args = {"context": context}
    return [
        (f"{prefix}/audio.m3u8", HlsPlaylistHandler, context_args),
        (
            rf"{prefix}/audio(?P<num>[0-9]+)\.?(?P<ext>\w*)",
            HlsFileHandler,
            context_args,
        ),
    ]
