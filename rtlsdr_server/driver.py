"""driver.py.

Driver for different subprocesses in the server.
"""
import os
import shlex
import asyncio
import subprocess
from collections import deque


async def _read_into_buffer(read_stream, buffer, encoding='utf-8'):
    while True:
        data = await read_stream.readline()
        if not data:
            return

        if encoding:
            data = data.decode(encoding)
        buffer.append(data)


class AbstractRtlDriver(object):

    def __init__(self):
        self._proc = None
        self._frequency = '107.3M'

    @property
    def frequency(self):
        return self._frequency

    def is_running(self):
        if self._proc:
            return self._proc.returncode is None
        return False

    def get_log(self):
        return ''

    async def start(self, frequency):
        raise NotImplementedError()

    def stop(self, force=False):
        if not self._proc:
            return
        if force:
            self._proc.kill()
        else:
            self._proc.terminate()

    async def wait(self):
        if not self._proc:
            return None
        proc_wait = asyncio.create_task(self._proc.wait())
        await asyncio.gather(proc_wait, self._stderr_fut)
        code = self._proc.returncode
        self._proc = None
        self._stderr_fut = None
        return code

    def reset(self):
        pass

    async def change_frequency(self, frequency, timeout=5):
        if self.is_running():
            self.stop()
            try:
                stop_fut = asyncio.create_task(self.wait())
                await asyncio.wait_for(
                    asyncio.shield(stop_fut),
                    # Wait for up to timeout seconds.
                    timeout)
            except asyncio.TimeoutError:
                # Failed to shutdown cleanly. Force close.
                self.stop(force=True)
                # This should work cleanly now.
                await stop_fut

        # At this point, the process is stopped, so update the frequency.
        self._frequency = frequency

        # Clear stderr, since the process is starting fresh.
        self.reset()

        # Start the process up again.
        await self.start(frequency)


class IcecastRtlFMDriver(AbstractRtlDriver):
    """Driver that runs rtl_fm through a pipeline to an Icecast server.

    This driver basically runs the following commands which should send
    audio data to an Icecast server:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast - | \
        sox -t raw -r 48k -es -b 16 -c 1 -V1 - -t mp3 - | \
        ffmpeg -i - -f mp3 -v 24 icecast://source@hackme:<host:port>/<path>

    This server then controls the frequency and can permit redirecting to
    the Icecast server internally, if desired.
    """

    def __init__(self, config):
        super(IcecastRtlFMDriver, self).__init__()
        self._rtlfm_exec_path = config.get('rtl_fm', '/usr/local/bin/rtl_fm')
        self._sox_exec_path = config.get('sox', '/usr/local/bin/sox')

        self._ffmpeg_exec_path = config.get('ffmpeg', '/usr/local/bin/ffmpeg')
        self._icecast_url = 'icecast://source:hackme@localhost:8000/radio'

        self._stderr_fut = None
        self._stderr_buffer = deque()
        self._stderr_encoding = 'utf-8'

    def get_log(self):
        return ''.join(self._stderr_buffer)

    def reset(self):
        # Reset the 'stderr' buffer, since we are starting a new process,
        # unless explicitly configured not to do so.
        self._stderr_buffer.clear()

    async def start(self, frequency):
        rtl_cmd = [
            self._rtlfm_exec_path, '-f', frequency, '-s',
            '200k', '-r', '48k', '-A', 'fast', '-'
        ]
        sox_cmd = [
            self._sox_exec_path, '-t', 'raw', '-r', '48k', '-es',
            '-b', '16', '-c', '1', '-V1', '-', '-t', 'mp3', '-'
        ]
        ffmpeg_cmd = [
            self._ffmpeg_exec_path, '-i', '-', '-f', 'mp3', '-v', '24',
            self._icecast_url
        ]
        cmd = ' | '.join([
            shlex.join(rtl_cmd),
            shlex.join(sox_cmd),
            shlex.join(ffmpeg_cmd)
        ])
        self._proc = await asyncio.create_subprocess_shell(
            cmd, stderr=subprocess.PIPE)
        self._stderr_fut = asyncio.create_task(_read_into_buffer(
            self._proc.stderr, self._stderr_buffer
        ))
        self._frequency = frequency


async def find_executable(cmd):
    cmd = shlex.join(['which', cmd])
    proc = await asyncio.create_subprocess_shell(cmd, stdout=subprocess.PIPE)
    stdout, _ = await proc.communicate()
    # Decode 'stdout' and return it.
    return stdout.decode('utf-8').strip()


async def find_pipeline_commands():
    rtl_fm_path, sox_path, ffmpeg_path = await asyncio.gather(
        find_executable('rtl_fm'),
        find_executable('sox'),
        find_executable('ffmpeg'))

    print("RTL FM: {}".format(rtl_fm_path))
    print("SOX: {}".format(sox_path))
    print("FFMPEG: {}".format(ffmpeg_path))


if __name__ == '__main__':
    asyncio.run(find_pipeline_commands())
