"""rtl_fm.py.

Driver
"""

import shlex
import asyncio
from dataclasses import dataclass
from pysdrweb.util import misc
from pysdrweb.drivers.base import AbstractPCMDriver


@dataclass
class RtlFmConfig:
    """Configuration for running with the 'rtl_fm' executable driver."""

    exec_path: str
    """Path to the executable."""

    default_frequency: str = "107.3M"
    """Set the default frequency when starting."""

    kb_buffer_size: int = 128
    """Buffer size for reading from 'rtl_fm' (in kB)."""

    framerate: int = 48000
    """Framerate (in Hz) for reading the PCM data."""


class RtlFmExecDriver(AbstractPCMDriver):
    """Driver that runs rtl_fm directly, then encodes the data on request.

    This driver basically runs:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast -

    then catches the input into a sliding window buffer. Then, the data
    will be encoded upon request into WAV, AIFF, or other formats.
    """

    @classmethod
    def from_config(cls, config_dict) -> "RtlFmExecDriver":
        rtlfm = config_dict.get("rtl_fm")
        if not rtlfm:
            rtlfm = misc.find_executable("rtl_fm")
            if not rtlfm:
                raise Exception("Could not find path to: rtl_fm")
        config = RtlFmConfig(exec_path=rtlfm)
        if "framerate" in config_dict:
            config.framerate = int(config_dict["framerate"])
        return cls(config)

    def __init__(self, config: RtlFmConfig):
        self._config = config
        # Extract the buffer size.
        super().__init__(
            # RTL-FM dumps its output into one channel, each sample 2 bytes
            # long. The frame-rate/sample-rate is somewhat configurable,
            # however.
            nchannels=1,
            sample_width=2,
            framerate=self._config.framerate,
            max_chunk_count=self._config.kb_buffer_size,
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
            shlex.quote(self._config.exec_path),
            # Configure the frequency here.
            "-f",
            shlex.quote(f"{frequency}"),
            "-s",
            "200k",
            "-r",
            shlex.quote(f"{self._config.framerate}"),
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
