"""rtl_native.py.

Native encoding driver for rtl_fm.

This uses built-in python utilities to encode the raw PCM
output from 'rtl_fm' into different formats.
"""
import asyncio
import subprocess
from collections import deque

# Stdlib Soundfile Imports
import wave
import aifc

# Tornado imports.
from tornado import iostream

# Local Imports
from pysdrweb.util.logger import get_child_logger
from pysdrweb.driver.common import (
    AbstractRtlDriver, read_lines_from_stream,
    UnsupportedFormatError, find_executable
)


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
        supported_formats = ['aiff', 'aifc', 'wav']
        super(RtlFMNativeDriver, self).__init__(supported_formats)

        # TODO -- Probably should not assume this path, but it works for now.
        self._rtlfm_exec_path = config['rtl_fm']

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
            self._rtlfm_exec_path, '-f', frequency, '-s',
            '200k', '-r', '48k', '-A', 'fast', '-'
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
        # Check for other formats first, because 'WAV' and 'AIFF' are handled
        # almost identically.
        writer = None
        try:
            file_handle = RequestFileHandle(req_handler)
            if fmt == 'wav':
                writer = wave.open(file_handle, 'wb')
                # Set the common parameters for the writer here.
                #
                # 1 channel (Mono)
                # 2 byte width sample
                # 48k framerate
                # 48000000 is an arbitrarily large number of frames.
                # 'NONE' compression type.
                # None for the compression name.
                writer.setparams((1, 2, 48000, 480000000, 'NONE', None))
                req_handler.set_header('Content-Type', 'audio/wav')
            elif fmt == 'aiff':
                writer = aifc.open(file_handle, 'wb')
                # Set the common parameters for the writer here.
                #
                # 1 channel (Mono)
                # 2 byte width sample
                # 48k framerate
                # 48000000 is an arbitrarily large number of frames.
                # 'NONE' compression type.
                # None for the compression name.
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(48000)
                writer.setnframes(48000000)
                writer.setcomptype(b'NONE', b'No compression.')
                req_handler.set_header('Content-Type', 'audio/aiff')
            elif fmt == 'aifc':
                writer = aifc.open(file_handle, 'wb')
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(48000)
                writer.setnframes(48000000)
                writer.setcomptype(b'G722', b'G.722 Compression.')
                req_handler.set_header('Content-Type', 'audio/aiff')
            else:
                raise UnsupportedFormatError(
                    'Format ({}) not supported by this driver!'.format(fmt))
        except UnsupportedFormatError:
            raise
        except Exception:
            logger.exception("Unknown error initializing format: %s", fmt)
            req_handler.send_status(500, "Internal Server Error.")
            req_handler.finish()
            return

        try:
            async for pcm_data in self.data_generator():
                # Writing data:
                writer.writeframesraw(pcm_data)
                await req_handler.flush()
        except iostream.StreamClosedError:
            return
        except Exception:
            logger.exception(
                "Unexpected error while sending data w/format: %s", fmt)
        finally:
            if writer:
                writer.close()
