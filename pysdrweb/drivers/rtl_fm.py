"""rtl_fm.py.

Driver
"""
import shlex
import asyncio
from pysdrweb.util import misc
from pysdrweb.drivers.base import AbstractRtlDriver


class RtlFmExecDriver(AbstractRtlDriver):
    """Driver that runs rtl_fm directly, then encodes the data on request.

    This driver basically runs:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast -

    then catches the input into a sliding window buffer. Then, the data
    will be encoded upon request into WAV, AIFF, or other formats.
    """

    @classmethod
    def from_config(cls, config):
        rtlfm = config.get("rtl_fm")
        if not rtlfm:
            rtlfm = misc.find_executable("rtl_fm")
            if not rtlfm:
                raise Exception("Could not find path to: rtl_fm")
            config["rtl_fm"] = rtlfm
        return cls(config)

    def __init__(self, config):
        # TODO -- Probably should not assume this path, but it works for now.
        self._rtlfm_exec_path = config["rtl_fm"]
        framerate = config.get("framerate", 48000)

        # Extract the buffer size.
        kb_buffer_size = int(config.get("kb_buffer_size", 128))
        super(RtlFmExecDriver, self).__init__(
            # RTL-FM dumps its output into one channel, each sample 2 bytes
            # long. The frame-rate/sample-rate is somewhat configurable,
            # however.
            nchannels=1,
            sample_width=2,
            framerate=framerate,
            max_chunk_count=kb_buffer_size,
        )

    async def _read_kb_chunks_into_buffer(self, stm):
        while not stm.at_eof():
            data = None
            try:
                data = await stm.readexactly(1024)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    data = e.partial
                else:
                    continue
            await self.add_pcm_chunk(data)

    async def start(self, frequency):
        rtl_cmd = [
            shlex.quote(self._rtlfm_exec_path),
            # Configure the frequency here.
            "-f",
            shlex.quote("{}".format(frequency)),
            "-s",
            "200k",
            "-r",
            shlex.quote("{}".format(self.framerate)),
            "-A",
            "fast",
            "-",
        ]
        rtl_proc = await asyncio.create_subprocess_exec(
            *rtl_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.add_process_handle(rtl_proc)
        # Read stderr into the log.
        self.add_awaitable(
            asyncio.create_task(
                misc.read_lines_from_stream(rtl_proc.stderr, self.add_log_line)
            )
        )
        # Read stdout into the buffer.
        self.add_awaitable(
            asyncio.create_task(self._read_kb_chunks_into_buffer(rtl_proc.stdout))
        )

        # Add an awaitable that will set the stop event once the process
        # is stopped.
        async def stop_on_exit():
            await rtl_proc.wait()
            self._stop_requested.set()

        self.add_awaitable(asyncio.create_task(stop_on_exit()))
        # Add an awaitable that will stall until the queues are consumed
        # before fully shutting down.
        self.add_awaitable(asyncio.create_task(self._wait_to_join_queues()))

