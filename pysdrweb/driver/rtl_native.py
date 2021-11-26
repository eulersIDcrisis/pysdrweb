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

# Local Imports
from pysdrweb.util.logger import get_child_logger
from pysdrweb.driver.common import AbstractRtlDriver


logger = get_child_logger('rtl_native')


class RtlFMNativeDriver(AbstractRtlDriver):
    """Driver that runs rtl_fm directly, then encodes the data on request.

    This driver basically runs:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast -

    then catches the input into a sliding window buffer. Then, the data
    will be encoded upon request into WAV, AIFF, or other formats.
    """

    def __init__(self, config):
        supported_formats = ['aiff', 'aifc', 'wav']
        super(RtlFMNativeDriver, self).__init__(supported_formats)

        # TODO -- Probably should not assume this path, but it works for now.
        self._rtlfm_exec_path = config.get('rtl_fm', '/usr/local/bin/rtl_fm')

        kb_buffer_size = int(config.get('kb_buffer_size', 128))
        self._pcm_buffer = deque(maxsize=kb_buffer_size)

        self._next_key = 1
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
        # Join the queues here and stall until the buffers are consumed.
        queue_awaitables = [
            asyncio.create_task(queue.join())
            for queue in self._buffer_queues.values()
        ]
        await asyncio.gather(*queue_awaitables)
        self._buffer_queues = dict()

    async def data_generator(self):
        qid = self._next_key
        self._next_key += 1
        try:
            # TODO -- We could cap the number of elements in this queue,
            # which effectively restricts the size of the buffer for each
            # connection, but not urgent for now.
            self._buffer_queues[qid] = asyncio.Queue()
            
        finally:
            self._buffer_queues.pop(qid, None)

    async def start(self, frequency):
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
        self.add_awaitable(asyncio.create_task(self._read_kb_chunks_into_buffer(
            rtl_proc.stdout)))

    async def process_request(self, req_handler, fmt):
        # Extract any timeout parameter.
        try:
            timeout = req_handler.get_argument('timeout')
            if timeout is not None:
                timeout = float(timeout)
        except Exception:
            req_handler.send_status(400, 'Bad "timeout" parameter!')
            return
        if fmt == 'wav':
            await self._write_wav_format(req_handler)
        elif fmt == 'aiff':
            await self._write_aiff_format(req_handler, compressed=False)
        elif fmt == 'aifc':
            await self._write_aiff_format(req_handler, compressed=True)
        else:
            raise UnsupportedFormatError(
                'Format ({}) not supported by this driver!'.format(fmt))

    async def _write_wav_format(self, req_handler):
        try:
            writer = wave.open(req_handler.write, 'wb')
            # Set the common parameters for the writer here.
            #
            # 1 channel (Mono)
            writer.setnchannels(1)
            # Frame rate: 48k (as configured via rtl_fm's arguments)
            writer.setframerate(48000)
            # Width (in bytes) of each sample.
            writer.setsampwidth(2)
            # Number of frames to send.
            writer.setnframes(-1)

            async for pcm_data in self.data_generator():
                writer.writerawrames(pcm_data)
                await req_handler.flush()
        finally:
            try:
                writer.close()
            except Exception:
                pass
