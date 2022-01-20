"""encoder.py.

Utilities for encoding PCM data into various formats asynchronously.
"""
import math
import asyncio
# Stdlib Soundfile Imports
import wave
import aifc

# Try the soundfile import. If it fails, set a flag, but don't fail;
# the native driver will then only support WAV, AIFF, and similar.
try:
    import soundfile
    _SOUNDFILE_IMPORTED = True

    # For now, let's only permit FLAC and OGG (if supported).
    SOUNDFILE_FORMATS = set(['FLAC', 'OGG']).intersection(
        soundfile.available_formats().keys()
    )
except ImportError:
    # Import failed, so set it false.
    _SOUNDFILE_IMPORTED = False
    SOUNDFILE_FORMATS = set()

# Local Imports
from pysdrweb.util import misc


_FORMAT_REGISTRY = {
    'WAV': 'audio/wav',
    'AIFF': 'audio/aiff',
    'AIFC': 'audio/aiff',
    'FLAC': 'audio/flac',
    'OGG': 'audio/ogg',
}


class UnsupportedFormatError(Exception):
    """Exception indicating an unsupported format."""


def get_mime_type_for_format(fmt):
    """Return the MIME type for the given format."""
    return _FORMAT_REGISTRY.get(fmt.upper(), 'application/octet-stream')


class RequestFileHandle(object):
    """Wrapper class to give 'web.RequestHandler' a 'file-like' interface.

    This fakes a seekable stream to avoid exceptions when using this with
    python's "wave" and "aifc" modules.
    """

    def __init__(self, handler):
        """Constructor that accepts the RequestHandler to write to."""
        self._handler = handler
        self._count = 0

    def write(self, data):
        """Write the given data to the handler."""
        self._handler.write(data)
        self._count += len(data)

    def close(self):
        """No-op when wrapping some RequestHandler instance."""
        pass

    def tell(self):
        """Return current bytes written.

        The number of bytes written is also the current position in the
        stream, since we do not permit seeking.
        """
        return self._count

    def seek(self, *args):
        """No-op, since this stream is not seekable."""
        pass

    def flush(self):
        pass

    async def async_flush(self):
        await self._handler.flush()


async def encode_from_driver(driver, file_obj, fmt, timeout,
                             async_flush=None, start_address=None):
    """Encode PCM data as generated by the given driver.

    The driver should include the information about the PCM data encoding,
    such as the channel count, sample rate, etc.

    An optional 'async_flush' kwarg is added to permit asynchronously flushing
    on each iteration (if desired). This permits writing out data in real-time
    without buffering concerns (useful when writing to a web.RequestHandler,
    for example).
    """
    # Ignore async_flush if it isn't a coroutine.
    if async_flush and not asyncio.iscoroutinefunction(async_flush):
        async_flush = None

    # Set the default start address, if it isn't set already.
    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    fmt = fmt.upper()
    # Check the format and decide what to do.
    if fmt == 'MP3':
        return await _process_mp3(
            driver, file_obj, timeout, async_flush=async_flush,
            start_address=start_address)
    elif fmt in set(['WAV', 'AIFF', 'AIFC']):
        return await _process_using_native_library(
            driver, file_obj, fmt, timeout,
            async_flush=async_flush, start_address=start_address)
    elif fmt in SOUNDFILE_FORMATS:
        return await _process_using_soundfile(
            driver, file_obj, fmt, timeout,
            async_flush=async_flush, start_address=start_address)
    else:
        raise UnsupportedFormatError('Unsupported format: {}'.format(fmt))


async def _process_using_native_library(
        driver, file_obj, fmt, timeout, async_flush=None, start_address=None):
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
    if fmt == 'WAV':
        writer = wave.open(file_obj, 'wb')
        writer.setnchannels(driver.nchannels)
        writer.setsampwidth(driver.sample_width)
        writer.setframerate(driver.framerate)
        writer.setnframes(frame_count)
        writer.setcomptype('NONE', 'No compression.')
    elif fmt == 'AIFF':
        writer = aifc.open(file_obj, 'wb')
        writer.setnchannels(driver.nchannels)
        writer.setsampwidth(driver.sample_width)
        writer.setframerate(driver.framerate)
        writer.setnframes(frame_count)
        writer.setcomptype(b'NONE', b'No compression.')
    elif fmt == 'AIFC':
        writer = aifc.open(file_obj, 'wb')
        writer.setnchannels(driver.nchannels)
        writer.setsampwidth(driver.sample_width)
        writer.setframerate(driver.framerate)
        writer.setnframes(frame_count)
        writer.setcomptype(b'G722', b'G.722 Compression.')
    else:
        raise UnsupportedFormatError(
            'Unsupported format: {}'.format(fmt))

    try:
        # Precalculate the frame size:
        framesize = driver.framesize
        async for seq_index, pcm_data in driver.pcm_item_generator(
                seq_index=start_address.seq_index):
            # Handle the special case when the start_address matches the
            # current frame; only return the remaining data, not the whole
            # array.
            if seq_index == start_address.seq_index:
                pcm_data = pcm_data[start_address.index:]

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

async def _process_using_soundfile(
        driver, file_obj, fmt, timeout, async_flush=None, start_address=None):
    """Process the request using 'soundfile'.

    This handles requests using python's "soundfile" import and the
    applicable 'libsndfile' dynamic library, if it is available. This
    supports more formats than just WAV, AIFF, and AU.
    """
    try:
        writer = soundfile.SoundFile(
            file_obj, mode='wb', format=fmt.upper(),
            samplerate=driver.framerate, channels=driver.nchannels)
    except Exception:
        raise UnsupportedFormatError('Unsupported format: {}'.format(fmt))

    if timeout is not None and timeout > 0:
        frame_count = int(math.ceil(driver.framerate * timeout))
    else:
        frame_count = None

    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    # Write out the frame data.
    try:
        # Precalculate the frame size
        framesize = driver.framesize
        async for seq_index, pcm_data in driver.pcm_item_generator(
                seq_index=start_address.seq_index):
            # Handle the special case when the start_address matches the
            # current frame; only return the remaining data, not the whole
            # array.
            if seq_index == start_address.seq_index:
                pcm_data = pcm_data[start_address.index:]
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
                writer.buffer_write(pcm_data[:idx], dtype='int16')

                # Remember that if we are still on the starting seq_index,
                # the full address needs to be adjusted appropriately.
                if start_address.seq_index == seq_index:
                    idx += start_address.index

                return misc.PCMBufferAddress(seq_index, idx)

            # Otherwise, just write out the full contents of this block, and
            # decrement the number of frames remaining.
            writer.buffer_write(pcm_data, dtype='int16')
            frame_count -= curr_count

            # Call any 'flush' handler if requested.
            if async_flush:
                await async_flush()
    finally:
        if writer:
            writer.close()


async def _process_mp3(driver, file_obj, timeout, quality=5,
                       async_flush=None, start_address=None):
    if quality < 2 or quality > 7:
        raise ValueError("Invalid MP3 Quality: {}".format(quality))

    if timeout is not None and timeout > 0:
        frame_count = int(math.ceil(driver.framerate * timeout))

    if start_address is None:
        start_address = misc.MIN_PCM_ADDRESS

    # Initialize the encoder.
    encoder = lameenc.Encoder()
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(driver.framerate)
    encoder.set_channels(driver.sample_width)
    encoder.set_quality(quality)  # 2-highest, 7-fastest

    # Precalculate the frame size
    framesize = driver.framesize
    async for seq_index, pcm_data in driver.pcm_item_generator(
            seq_index=start_address.seq_index):
        # Handle the special case when the start_address matches the
        # current frame; only return the remaining data, not the whole
        # array.
        if seq_index == start_address.seq_index:
            pcm_data = pcm_data[start_address.index:]
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

            mp3_data = encoder.encode(interleaved_pcm_data)
            writer.write(mp3_data)
            mp3_data = encoder.flush()
            writer.write(mp3_data)

            # Remember that if we are still on the starting seq_index,
            # the full address needs to be adjusted appropriately.
            if start_address.seq_index == seq_index:
                idx += start_address.index

            return misc.PCMBufferAddress(seq_index, idx)

        # Otherwise, just write out the full contents of this block, and
        # decrement the number of frames remaining.
        mp3_data = encoder.encode(interleaved_pcm_data)
        writer.write(mp3_data)

        if frame_count is not None:
            frame_count -= curr_count

        # Call any 'flush' handler if requested.
        if async_flush:
            await async_flush()
