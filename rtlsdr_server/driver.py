"""driver.py.

Driver for different subprocesses in the server.
"""
import os
import shlex
import logging
import asyncio
import subprocess
from collections import deque
from contextlib import AsyncExitStack
from urllib.parse import urlsplit
from tornado import httpclient, httputil, iostream, tcpclient


logger = logging.getLogger('driver')


class UnsupportedFormatError(Exception):
    """Exception indicating an unsupported format."""


async def _read_into_buffer(read_stream, buffer, encoding='utf-8'):
    while True:
        data = await read_stream.readline()
        if not data:
            return

        if encoding:
            data = data.decode(encoding)
        buffer.append(data)


class AbstractRtlDriver(object):

    def __init__(self, formats):
        self._supported_formats = set(formats)
        self._proc = None
        self._frequency = '107.3M'

    @property
    def supported_formats(self):
        return self._supported_formats

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
        # Process already exited. Nothing to stop.
        if self._proc.returncode is not None:
            return
        if force:
            self._proc.kill()
        else:
            self._proc.terminate()

    async def wait(self):
        if not self._proc:
            return None
        if self._proc.returncode is not None:
            return self._proc.returncode
        proc_wait = asyncio.create_task(self._proc.wait())
        await asyncio.gather(proc_wait, self._stderr_fut)
        code = self._proc.returncode
        self._stderr_fut = None
        return code

    def reset(self):
        pass

    async def change_frequency(self, frequency, timeout=5):
        if self.is_running():
            logger.info("Stopping RTL-FM pipeline.")
            self.stop(force=True)
            await self.wait()
            logger.info("Shutdown current RTL-FM pipeline.")

        # At this point, the process is stopped, so update the frequency.
        self._frequency = frequency

        logger.info("Changing frequency to: %s", self._frequency)

        # Clear stderr, since the process is starting fresh.
        self.reset()

        # Start the process up again.
        await self.start(self._frequency)

    async def process_request(self, req_handler, fmt):
        raise NotImplementedError()


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
        # Supported formats are technically based on the Icecast configuration
        # but for now, we'll assume MP3.
        super(IcecastRtlFMDriver, self).__init__(['mp3'])
        self._rtlfm_exec_path = config.get('rtl_fm', '/usr/local/bin/rtl_fm')
        self._sox_exec_path = config.get('sox', '/usr/local/bin/sox')

        self._ffmpeg_exec_path = config.get('ffmpeg', '/usr/local/bin/ffmpeg')
        self._icecast_url = config.get(
            'icecast_url', 'icecast://source:hackme@localhost:8000/radio')
        self._client_url = config.get(
            'client_url', 'http://localhost:8000/radio')

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
        logger.info("Running: %s", cmd)
        self._proc = await asyncio.create_subprocess_shell(
            cmd, stderr=subprocess.PIPE)
        self._stderr_fut = asyncio.create_task(_read_into_buffer(
            self._proc.stderr, self._stderr_buffer
        ))
        self._frequency = frequency

    async def process_request(self, req_handler, fmt):
        # Proxy this request to the icecast server. This currently only
        # supports GET, since that is all that should be necessary.
        #
        # For this case, we detach entirely from 'req_handler' so we can
        # send the raw contents (with minimal buffering) from the icecast
        # server.
        icecast_socket = None
        try:
            # Now, open a raw TCP connection to the Icecast server and send
            # the HTTP request manually. This helps reduce the buffering on
            # the response.
            _, netloc, path, _, _ = urlsplit(self._client_url)
            args = netloc.split(':', 1)
            if len(args) == 1:
                host = args[0]
                port = 80
            else:
                host = args[0]
                port = int(args[1])

            icecast_socket = await tcpclient.TCPClient().connect(
                host, port)
        except iostream.StreamClosedError:
            logger.error("Icecast server URL failed: %s", self._client_url)
            req_handler.set_status(500)
            req_handler.write(dict(
                status=500, message="Internal Server Error!"))
            return
        except Exception:
            # If the connection fails, then return a 500 status code.
            logger.exception("Unknown error proxying Icecast request!")
            req_handler.set_status(500)
            req_handler.write(dict(
                status=500, message="Internal Server Error!"))
            return

        # At this point, we have a connection, so manually handle the stream
        # to avoid default buffering issues, which is what this 'detach()'
        # call does.
        stream = req_handler.detach()
        try:
            # Write a basic HTTP request. Note that the headers are determined
            # loosely by curling the icecast server.
            req_line = 'GET {} HTTP/1.1\r\n'.format(path)
            await icecast_socket.write(req_line.encode('utf-8'))
            # Write the headers.
            host_line = 'Host: {}\r\n'.format(netloc)
            await icecast_socket.write(host_line.encode('utf-8'))
            # For now, we'll just assume this is a 'curl' connection. We can
            # change this later, but it works for now. Icecast probably does
            # not care too much about this header.
            await icecast_socket.write(b'User-Agent: curl/7.68.0\r\n')
            await icecast_socket.write(b'Accept: */*\r\n')
            # End of the headers.
            await icecast_socket.write(b'\r\n')
            # At this point, we should read the response. Read in chunks of
            # 1024 bytes; this buffer is small enough to return in realtime.
            buff = bytearray(1024)
            while True:
                count = await icecast_socket.read_into(buff, partial=True)
                await stream.write(buff[:count])
        except iostream.StreamClosedError:
            logger.info("Closing connection.")
            stream.close()
        except Exception:
            logger.exception("Unexpected exception!")
        finally:
            # Close the connection to the icecast server, then close the other
            # connections as appropriate.
            if icecast_socket:
                icecast_socket.close()


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
