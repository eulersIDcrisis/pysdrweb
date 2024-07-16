"""pynative.py.

"Native" python encoders (i.e. via WAVE or similar that only use the python
standard library).
"""

from collections.abc import Sequence, Awaitable, Callable
from typing import Optional, Any, BinaryIO
import math
from contextlib import ExitStack

# Still in the python standard library.
import wave

# PySDRWeb Imports
from pysdrweb.util import misc
from pysdrweb.encoders.base import BaseEncoder, UnsupportedFormatError


# Helpers to abstract initializing the file to write encoded data to.
def _open_wave_file(file_obj, driver, frame_count):
    with ExitStack() as exit_stack:
        writer = wave.open(file_obj, "wb")
        exit_stack.callback(writer.close)
        writer.setnchannels(driver.nchannels)
        writer.setsampwidth(driver.sample_width)
        writer.setframerate(driver.framerate)
        writer.setnframes(frame_count)
        writer.setcomptype("NONE", "No compression.")

        # No errors, so return the driver without closing it.
        exit_stack.pop_all()
    return writer


_FORMAT_FILE_REGISTRY: dict[str, Callable[[BinaryIO, Any, int]]] = {
    "WAV": _open_wave_file
}
try:
    import aifc

    def _open_aiff_file(file_obj, driver, frame_count):
        with ExitStack() as exit_stack:
            writer = aifc.open(file_obj, "wb")
            exit_stack.callback(writer.close)
            writer.setnchannels(driver.nchannels)
            writer.setsampwidth(driver.sample_width)
            writer.setframerate(driver.framerate)
            writer.setnframes(frame_count)
            writer.setcomptype(b"NONE", b"No compression.")
            # No errors, so return the driver without closing it.
            exit_stack.pop_all()
        return writer

    def _open_aifc_file(file_obj, driver, frame_count):
        with ExitStack() as exit_stack:
            writer = aifc.open(file_obj, "wb")
            exit_stack.callback(writer.close)
            writer.setnchannels(driver.nchannels)
            writer.setsampwidth(driver.sample_width)
            writer.setframerate(driver.framerate)
            writer.setnframes(frame_count)
            writer.setcomptype(b"G722", b"G.722 Compression.")
            # No errors, so return the driver without closing it.
            exit_stack.pop_all()
        return writer

    _FORMAT_FILE_REGISTRY["AIFF"] = _open_aiff_file
    _FORMAT_FILE_REGISTRY["AIFC"] = _open_aifc_file

except ImportError:
    # Ignore the AIFF/AIFC formats which are no longer included with the
    # python standard library.
    pass


#
# Generic "Native" Encoder
#
class StandardLibraryEncoder(BaseEncoder):
    _supported_formats = frozenset(_FORMAT_FILE_REGISTRY.keys())

    @classmethod
    def get_supported_formats(cls) -> Sequence[str]:
        return cls._supported_formats

    async def encode(
        self,
        stream,
        format_type: str,
        timeout: Optional[float] = None,
        async_flush: Optional[Callable[[None], Awaitable[None]]] = None,
        start_address: Optional[misc.PCMBufferAddress] = None,
    ) -> None:
        return await _process_using_native_library(
            self.driver,
            stream,
            format_type,
            timeout=timeout,
            async_flush=async_flush,
            start_address=start_address,
        )


async def _process_using_native_library(
    driver, file_obj, fmt, timeout, async_flush=None, start_address=None
) -> misc.PCMBufferAddress:
    """Write out the PCM data using the native python 3 sound libraries.

    The formats here should always be supported and are comparatively
    simple, but are also generally large/uncompressed.
    """
    # The 'timeout' parameter is the number of seconds. If negative
    # (or None), this implies indefinite. Since we cannot _actually_
    # do that, we set the number of frames to some arbitrarily large
    # number.
    if timeout is not None and timeout > 0:
        frame_count = int(math.ceil(driver.framerate * timeout))
    else:
        # Some arbitrarily large number, mapping to 1000 minutes.
        frame_count = driver.framerate * 60 * 1000

    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    writer = None
    open_func = _FORMAT_FILE_REGISTRY.get(fmt)
    if open_func is None:
        raise UnsupportedFormatError(f"Unsupported format: {fmt}")

    writer = open_func(file_obj, driver, frame_count)
    try:
        # Precalculate the frame size:
        framesize = driver.framesize
        async for seq_index, pcm_data in driver.pcm_item_generator(
            seq_index=start_address.seq_index
        ):
            # Handle the special case when the start_address matches the
            # current frame; only return the remaining data, not the whole
            # array.
            if seq_index == start_address.seq_index:
                pcm_data = pcm_data[start_address.index :]

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
                writer.writeframesraw(pcm_data[:idx])

                # Remember that if we are still on the starting seq_index,
                # the full address needs to be adjusted appropriately.
                if start_address.seq_index == seq_index:
                    idx += start_address.index

                return misc.PCMBufferAddress(seq_index, idx)

            # Otherwise, just write out the full contents of this block, and
            # decrement the number of frames remaining.
            writer.writeframesraw(pcm_data)
            frame_count -= curr_count

            # Call any 'flush' handler if requested.
            if async_flush:
                await async_flush()
    finally:
        if writer:
            writer.close()
