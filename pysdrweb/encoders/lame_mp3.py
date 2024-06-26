"""lame_mp3.py.

Module to handle encoing using the LAME MP3 library.
"""

import math
import time
from typing import Awaitable, Callable
import lameenc
from pysdrweb.util import misc
from pysdrweb.encoders.base import BaseEncoder
from pysdrweb.drivers import AbstractRtlDriver


class Mp3Encoder(BaseEncoder):

    _supported_formats = ("MP3",)

    def __init__(self, driver: AbstractRtlDriver, quality=7) -> None:
        super().__init__(driver)
        self.quality = quality

    @classmethod
    def get_supported_formats(self) -> tuple[str]:
        return Mp3Encoder._supported_formats

    async def encode(
        self,
        stream,
        format_type: str,
        timeout: float | None = None,
        async_flush: Callable[[], Awaitable[None]] = None,
        start_address=None,
    ) -> misc.PCMBufferAddress:
        assert format_type.lower() == "mp3"
        return await _process_mp3(
            self.driver,
            stream,
            timeout,
            self.quality,
            async_flush=async_flush,
            start_address=start_address,
        )


async def _process_mp3(
    driver,
    file_obj,
    timeout: float | None,
    quality: int = 5,
    async_flush=None,
    start_address=None,
) -> misc.PCMBufferAddress:
    if quality < 2 or quality > 7:
        raise ValueError("Invalid MP3 Quality: {}".format(quality))

    if timeout is not None and timeout > 0:
        frame_count = int(math.ceil(driver.framerate * timeout))
    else:
        frame_count = None

    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    # Initialize the encoder.
    encoder = lameenc.Encoder()
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(driver.framerate)
    encoder.set_channels(driver.nchannels)
    encoder.set_quality(quality)  # 2-highest, 7-fastest

    # Precalculate the frame size
    framesize = driver.framesize

    # Store the time too and only flush the MP3 buffer every 0.5 seconds.
    curr_ts = time.time()
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
        if frame_count is not None and curr_count > frame_count:
            # Otherwise, the final frames are available. Write the final
            # frames that are needed, then return a PCMBufferAddress to the
            # end of this buffer.
            idx = frame_count * framesize

            mp3_data = encoder.encode(pcm_data)
            mp3_data = encoder.flush()
            file_obj.write(bytes(mp3_data))

            # Remember that if we are still on the starting seq_index,
            # the full address needs to be adjusted appropriately.
            if start_address.seq_index == seq_index:
                idx += start_address.index

            return misc.PCMBufferAddress(seq_index, idx)

        # Otherwise, just write out the full contents of this block, and
        # decrement the number of frames remaining.
        mp3_data = encoder.encode(pcm_data)
        file_obj.write(bytes(mp3_data))

        if frame_count is not None:
            frame_count -= curr_count

        if async_flush:
            new_ts = time.time()
            if new_ts > curr_ts + 1:
                curr_ts = new_ts
                await async_flush()
