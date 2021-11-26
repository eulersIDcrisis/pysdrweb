"""common.py.

Common utilities for different web radio drivers.
"""
import shlex
import asyncio
import subprocess
from collections import deque


class UnsupportedFormatError(Exception):
    """Exception indicating an unsupported format."""


async def read_lines_from_stream(read_stream, callback, encoding='utf-8'):
    """Parse the given stream, and invoke 'callback' for each line parsed.

    This continues parsing from the stream until the stream is closed and
    will decode the line into a string ('utf-8' by default) before invoking
    the callback. (Setting 'encoding=None' will pass the raw bytes to the
    callback if the caller does not want to decode the line.)
    """
    while not read_stream.at_eof():
        data = await read_stream.readline()
        if not data:
            return
        if encoding:
            data = data.decode(encoding)
        callback(data)


async def close_pipe_on_exit(proc, fd):
    """Helper that will close the given fd (pipe) when 'proc' finishes.

    Useful when chaining processes because sometimes the write side of the
    pipe needs to be explicitly closed for a clean shutdown.
    """
    await proc.wait()
    os.close(stm)


class AbstractRtlDriver(object):

    def __init__(self, formats):
        self._supported_formats = set(formats)
        self._processes = []
        self._futures = []
        self._frequency = '107.3M'
        self._log = deque()

    @property
    def supported_formats(self):
        return self._supported_formats

    @property
    def frequency(self):
        return self._frequency

    def is_running(self):
        for proc in self._processes:
            if proc.returncode is None:
                return True
        return False

    def get_log(self):
        return ''.join(self._log)

    def add_log_line(self, line):
        self._log.append(line)

    def add_process_handle(self, proc_handle):
        self._processes.append(proc_handle)
        # Also await the process in a future.
        self._futures.append(asyncio.create_task(proc_handle.wait()))

    def add_awaitable(self, fut):
        self._futures.append(fut)

    async def start(self, frequency):
        raise NotImplementedError()

    def stop(self):
        for proc in self._processes:
            if proc.returncode is None:
                proc.terminate()

    async def wait(self):
        if self._futures:
            await asyncio.gather(*self._futures)

    async def reset(self):
        """Reset the driver, by stopping (and waiting) for it to stop.

        After stopping the driver, this should cleanup any internal
        resources so that the driver is safe to 'start()' again.
        """
        if self.is_running():
            self.stop()
            await self.wait()
        self._futures = []
        self._processes = []
        self._log.clear()

    async def change_frequency(self, frequency, timeout=5):
        # Reset the driver first, before starting it up again.
        await self.reset()

        # At this point, the process is stopped, so update the frequency.
        self._frequency = frequency

        # Start the process up again.
        await self.start(self._frequency)

    async def process_request(self, req_handler, fmt):
        raise NotImplementedError()


#
# Miscellaneous Utilities
#
def find_executable(cmd):
    cmd = shlex.join(['which', shlex.quote(cmd)])
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
    if proc.stdout:
        return proc.stdout.decode('utf-8').strip()
    return None


# async def find_executable(cmd):
#     cmd = shlex.join(['which', cmd])
#     proc = await asyncio.create_subprocess_shell(cmd, stdout=subprocess.PIPE)
#     stdout, _ = await proc.communicate()
#     # Decode 'stdout' and return it.
#     return stdout.decode('utf-8').strip()
