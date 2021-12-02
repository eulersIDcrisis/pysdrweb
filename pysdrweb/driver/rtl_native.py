"""rtl_native.py.

Native encoding driver for rtl_fm.

This uses built-in python utilities to encode the raw PCM
output from 'rtl_fm' into different formats.
"""
import math
import asyncio
import subprocess
from collections import deque

# Stdlib Soundfile Imports
import wave
import aifc

# Try the soundfile import. If it fails, set a flag, but don't fail;
# the native driver will then only support WAV, AIFF, and similar.
try:
    import soundfile
    _SOUNDFILE_IMPORTED = True
except ImportError:
    # Import failed, so set it false.
    _SOUNDFILE_IMPORTED = False

# Tornado imports.
from tornado import iostream

# Local Imports
from pysdrweb.util.logger import get_child_logger
from pysdrweb.driver.common import (
    AbstractRtlDriver, read_lines_from_stream,
    UnsupportedFormatError, find_executable
)


_NATIVE_FORMATS = set(['AIFF', 'AIFC', 'WAV'])
"""Native formats supported via a raw python interface."""


logger = get_child_logger('rtl_native')


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


class RtlFMNativeDriver(AbstractRtlDriver):
    """Driver that runs rtl_fm directly, then encodes the data on request.

    This driver basically runs:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast -

    then catches the input into a sliding window buffer. Then, the data
    will be encoded upon request into WAV, AIFF, or other formats.
    """

    name = 'native'

    @classmethod
    def from_config(cls, config):
        rtlfm = config.get('rtl_fm')
        if not rtlfm:
            rtlfm = find_executable('rtl_fm')
            if not rtlfm:
                raise Exception("Could not find path to: rtl_fm")
            config['rtl_fm'] = rtlfm
        return cls(config)

    def __init__(self, config):
        supported_formats = list(_NATIVE_FORMATS)
        if _SOUNDFILE_IMPORTED:
            format_mapping = soundfile.available_formats()

            # For now, let's only support 'OGG' and 'FLAC'. We could
            # possibly open this up to all of them, but this is okay for
            # now.
            if 'OGG' in format_mapping:
                supported_formats.append('OGG')
            if 'FLAC' in format_mapping:
                supported_formats.append('FLAC')
        super(RtlFMNativeDriver, self).__init__(supported_formats)

        # TODO -- Probably should not assume this path, but it works for now.
        self._rtlfm_exec_path = config['rtl_fm']

        # Common encoding parameters.
        self._framerate = 48000
        # Data from RTL-FM is PCM: Mono, 2-byte, unsigned little endian.
        self._nchannels = 1
        self._sample_width = 2

        kb_buffer_size = int(config.get('kb_buffer_size', 128))
        self._pcm_buffer = deque(maxlen=kb_buffer_size)

        self._next_key = 1
        self._stop_requested = asyncio.Event()

        # Stores the current list of places to send rtl_fm's buffered data. It
        # is protected by a condition variable to notify relevant callers for
        # a clean shutdown.
        self._buffer_cond = asyncio.Condition()
        self._buffer_queues = dict()

    async def _read_kb_chunks_into_buffer(self, stm):
        while not stm.at_eof():
            try:
                data = await stm.readexactly(1024)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    data = e.partial
                else:
                    continue
            for queue in self._buffer_queues.values():
                queue.put_nowait(data)
        # Push 'None' into every queue; this signals to stop iterating.
        for queue in self._buffer_queues.values():
            queue.put_nowait(None)

    async def _wait_to_join_queues(self):
        # First, wait for the stop event. We don't want to wait for the queues
        # until a stop is actually requested.
        await self._stop_requested.wait()
        # Now, wait for the queues to empty.
        async with self._buffer_cond:
            while len(self._buffer_queues) > 0:
                await self._buffer_cond.wait()

    async def data_generator(self):
        if self._stop_requested.is_set():
            return
        qid = self._next_key
        self._next_key += 1
        try:
            # TODO -- We could cap the number of elements in this queue,
            # which effectively restricts the size of the buffer for each
            # connection, but not urgent for now.
            iter_queue = asyncio.Queue()

            # Preload the queue with all of the data currently in the deque.
            for data in self._pcm_buffer:
                iter_queue.put_nowait(data)

            # Add this buffer to receive new data from the handler as it is
            # received. This is protected by a condition variable, so acquire
            # that first.
            async with self._buffer_cond:
                self._buffer_queues[qid] = iter_queue
                self._buffer_cond.notify_all()

            # Now, consume data from the queue.
            while True:
                # If 'None' is parsed out from the queue, this is our cue (ha)
                # to exit.
                data = await iter_queue.get()
                if data is None:
                    return
                yield data
        finally:
            async with self._buffer_cond:
                self._buffer_queues.pop(qid, None)
                self._buffer_cond.notify_all()

    async def start(self, frequency):
        self._stop_requested.clear()
        rtl_cmd = [
            self._rtlfm_exec_path, '-f', '{}'.format(frequency),
            '-s', '200k', '-r', '{}'.format(self._framerate),
            '-A', 'fast', '-'
        ]
        rtl_proc = await asyncio.create_subprocess_exec(
            *rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.add_process_handle(rtl_proc)
        # Read stderr into the log.
        self.add_awaitable(asyncio.create_task(read_lines_from_stream(
            rtl_proc.stderr, self.add_log_line)))
        # Read stdout into the buffer.
        self.add_awaitable(asyncio.create_task(
            self._read_kb_chunks_into_buffer(rtl_proc.stdout)
        ))
        # Add an awaitable that will set the stop event once the process
        # is stopped.
        async def stop_on_exit():
            await rtl_proc.wait()
            self._stop_requested.set()
        self.add_awaitable(asyncio.create_task(stop_on_exit()))
        # Add an awaitable that will stall until the queues are consumed
        # before fully shutting down.
        self.add_awaitable(asyncio.create_task(
            self._wait_to_join_queues()
        ))

    async def process_request(self, req_handler, fmt, timeout):
        try:
            fmt = fmt.upper()
            if fmt not in self.supported_formats:
                raise UnsupportedFormatError(
                    'Unsupported format: {}'.format(fmt))
            if fmt.upper() in _NATIVE_FORMATS:
                # Use the native python imports.
                await self._process_using_native_library(
                    req_handler, fmt, timeout)
            else:
                await self._process_using_soundfile(
                    req_handler, fmt, timeout)
        except UnsupportedFormatError as ufe:
            req_handler.send_status(400, "Unsupported format: {}".format(fmt))
        except Exception:
            logger.exception("Error in rtl_native request!")
            req_handler.send_status(500, "Internal Server Error.")

    async def _process_using_native_library(self, req_handler, fmt, timeout):
        # The 'timeout' parameter is the number of seconds. If negative
        # (or None), this implies indefinite. Since we cannot _actually_
        # do that, we set the number of frames to some arbitrarily large
        # number.
        if timeout is not None and timeout > 0:
            frame_count = int(math.ceil(48000 * timeout))
        else:
            # Some arbitrarily large number, mapping to 1000 minutes.
            frame_count = 48000000

        writer = None
        try:
            # The common parameters for the writer here.
            #
            # 1 channel (Mono)
            # 2 byte width sample
            # 48k framerate
            # 48000000 is an arbitrarily large number of frames.
            # 'NONE' compression type.
            # None for the compression name.
            file_handle = RequestFileHandle(req_handler)
            if fmt == 'WAV':
                writer = wave.open(file_handle, 'wb')
                writer.setnchannels(self._nchannels)
                writer.setsampwidth(self._sample_width)
                writer.setframerate(self._framerate)
                writer.setnframes(frame_count)
                writer.setcomptype('NONE', 'No compression.')
                req_handler.set_header('Content-Type', 'audio/wav')
            elif fmt == 'AIFF':
                writer = aifc.open(file_handle, 'wb')
                # Set the common parameters for the writer here.
                #
                # 1 channel (Mono)
                # 2 byte width sample
                # 48k framerate
                # 'NONE' compression type.
                # None for the compression name.
                writer.setnchannels(self._nchannels)
                writer.setsampwidth(self._sample_width)
                writer.setframerate(self._framerate)
                writer.setnframes(frame_count)
                writer.setcomptype(b'NONE', b'No compression.')
                req_handler.set_header('Content-Type', 'audio/aiff')
            elif fmt == 'AIFC':
                writer = aifc.open(file_handle, 'wb')
                writer.setnchannels(self._nchannels)
                writer.setsampwidth(self._sample_width)
                writer.setframerate(self._framerate)
                writer.setnframes(frame_count)
                writer.setcomptype(b'G722', b'G.722 Compression.')
                req_handler.set_header('Content-Type', 'audio/aiff')
            else:
                raise Exception("Internal error in fmt logic!")
        except Exception:
            logger.exception("Unknown error initializing format: %s", fmt)
            req_handler.send_status(500, "Internal Server Error.")
            return

        try:
            frame_num = 0
            async for pcm_data in self.data_generator():
                # Writing data:
                writer.writeframesraw(pcm_data)
                await req_handler.flush()
                frame_num += (len(pcm_data) / (
                    self._sample_width * self._nchannels))
                # Stop writing frames if we've exceeded the frame count.
                if frame_num >= frame_count:
                    return
        except iostream.StreamClosedError:
            return
        except Exception:
            logger.exception(
                "Unexpected error while sending data w/format: %s", fmt)
        finally:
            if writer:
                writer.close()

    async def _process_using_soundfile(self, req_handler, fmt, timeout):
        """Process the request using 'soundfile'.

        This handles requests using python's "soundfile" import and the
        applicable 'libsndfile' dynamic library, if it is available. This
        supports more formats than just WAV, AIFF, and AU.
        """
        try:
            file_handle = RequestFileHandle(req_handler)
            writer = soundfile.SoundFile(
                file_handle, mode='w', format=fmt.upper(),
                samplerate=self._framerate, channels=self._nchannels)
        except Exception:
            raise UnsupportedFormatError('Unsupported format: {}'.format(fmt))

        if timeout is not None and timeout > 0:
            frame_count = int(math.ceil(48000 * timeout))
        else:
            frame_count = None

        # Write out the frame data.
        try:
            frame_num = 0
            async for pcm_data in self.data_generator():
                writer.buffer_write(pcm_data, dtype='int16')
                await req_handler.flush()

                # Count the number of frames, like with the native driver.
                # The contents here are likely
                frame_num += (len(pcm_data) / (
                    self._sample_width * self._nchannels))
                # Stop writing frames if we've exceeded the frame count.
                if frame_count is not None and frame_num >= frame_count:
                    # Explicitly close the writer, then set it None. This
                    # tells the soundfile to flush any remaining contents
                    # to the stream before we exit the request.
                    writer.close()
                    writer = None
                    return
        except iostream.StreamClosedError:
            return
        except Exception:
            logger.exception(
                "Unexpected error while sending data w/format: %s", fmt)
        finally:
            if writer:
                writer.close()
