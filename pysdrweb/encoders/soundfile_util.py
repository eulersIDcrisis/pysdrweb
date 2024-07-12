"""soundfile_util.py.

Encoders using python's 'soundfile' library.
"""

from typing import Awaitable, Callable
from collections.abc import Sequence
import math
import soundfile
from pysdrweb.util import misc
from pysdrweb.drivers import AbstractRtlDriver
from pysdrweb.encoders.base import UnsupportedFormatError, BaseEncoder

# NOTE: The available formats that soundfile supports should be at:
# soundfile.available_formats()
#
# For now, we'll just explicitly assume FLAC and OGG.
_FORMAT_TYPES = frozenset(["FLAC", "OGG"]).intersection(
    soundfile.available_formats().keys()
)


class SoundfileEncoder(BaseEncoder):
    """Encoder that uses the ``soundfile`` module."""

    @classmethod
    def get_supported_formats(cls) -> Sequence[str]:
        return _FORMAT_TYPES

    async def encode(
        self,
        stream,
        format_type: str,
        timeout: float | None = None,
        async_flush: Callable[[], Awaitable[None]] = None,
        start_address=None,
    ):
        return await _process_using_soundfile(
            self.driver,
            stream,
            format_type,
            timeout,
            async_flush=async_flush,
            start_address=start_address,
        )


async def _process_using_soundfile(
    driver, file_obj, fmt, timeout, async_flush=None, start_address=None
):
    """Process the request using 'soundfile'.

    This handles requests using python's "soundfile" import and the
    applicable 'libsndfile' dynamic library, if it is available. This
    supports more formats than just WAV, AIFF, and AU.
    """
    try:
        writer = soundfile.SoundFile(
            file_obj,
            mode="wb",
            format=fmt.upper(),
            samplerate=driver.framerate,
            channels=driver.nchannels,
        )
    except Exception as exc:
        raise UnsupportedFormatError(f"Unsupported format: {fmt}") from exc

    if timeout is not None and timeout > 0:
        frame_count = int(math.ceil(driver.framerate * timeout))
    else:
        # Some arbitrarily large number, mapping to 1000 minutes.
        frame_count = driver.framerate * 60 * 1000

    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    # Write out the frame data.
    try:
        # Precalculate the frame size
        framesize = driver.framesize
        async for seq_index, pcm_data in driver.pcm_item_generator(
            seq_index=start_address.seq_index
        ):
            # Handle the special case when the start_address matches the
            # current frame; only return the remaining data, not the whole
            # array.
            if seq_index == start_address.seq_index:
                pcm_data = pcm_data[start_address.index :]
                # No data in this chunk.
                if len(pcm_data) <= 0:
                    continue

            curr_count = len(pcm_data) // framesize

            # If the current number of frames (curr_count) exceeds the number
            # of remaining frames (frame_count), then we are at the end and
            # are ready to exit. Write the remaining frames, then return a
            # PCMBufferAddress pointing to the end (of the written data) and
            # exit.
            if curr_count >= frame_count:
                # Otherwise, the final frames are available. Write the final
                # frames that are needed, then return a PCMBufferAddress to the
                # end of this buffer.
                idx = frame_count * framesize
                writer.buffer_write(pcm_data[:idx], dtype="int16")

                # Remember that if we are still on the starting seq_index,
                # the full address needs to be adjusted appropriately.
                if start_address.seq_index == seq_index:
                    idx += start_address.index

                return misc.PCMBufferAddress(seq_index, idx)

            # Otherwise, just write out the full contents of this block, and
            # decrement the number of frames remaining.
            writer.buffer_write(pcm_data, dtype="int16")
            frame_count -= curr_count

            # Call any 'flush' handler if requested.
            if async_flush:
                await async_flush()
    finally:
        if writer:
            writer.close()
