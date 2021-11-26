"""icecast.py.

Driver that proxies 'rtl_fm' output to an icecast server.

Currently requires 'rtl_fm', 'sox', and 'ffmpeg' to be installed
(with mpeg encoding capabilities). Future versions of this could
support other things.
"""
import os
import asyncio
import subprocess
from urllib.parse import urlsplit, urlunsplit
# External Imports
from tornado import tcpclient
# Local Imports
from pysdrweb.util.logger import get_child_logger
from pysdrweb.driver.common import (
    AbstractRtlDriver, read_lines_from_stream, close_pipe_on_exit
)

# Set the logger for the icecast driver.
logger = get_child_logger('icecast_driver')


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

    async def start(self, frequency=None):
        if frequency is None:
            frequency = self._frequency
        rtl_cmd = [
            self._rtlfm_exec_path, '-f', frequency, '-s',
            '200k', '-r', '48k', '-A', 'fast', '-'
        ]
        rtl_read_fd, rtl_write_fd = os.pipe()
        rtl_proc = await asyncio.create_subprocess_exec(
            *rtl_cmd, stdout=rtl_write_fd, stderr=subprocess.PIPE
        )
        self.add_process_handle(rtl_proc)
        self.add_awaitable(asyncio.create_task(
            read_lines_from_stream(rtl_proc.stderr, self.add_log_line)))
        self.add_awaitable(asyncio.create_task(
            close_pipe_on_exit(rtl_proc, rtl_write_fd)))

        sox_cmd = [
            self._sox_exec_path, '-t', 'raw', '-r', '48k', '-es',
            '-b', '16', '-c', '1', '-V1', '-', '-t', 'mp3', '-'
        ]
        sox_read_fd, sox_write_fd = os.pipe()
        sox_proc = await asyncio.create_subprocess_exec(
            *sox_cmd, stdin=rtl_read_fd,
            stdout=sox_write_fd,
            stderr=subprocess.PIPE
        )
        self.add_process_handle(sox_proc)
        self.add_awaitable(asyncio.create_task(
            read_lines_from_stream(sox_proc.stderr, self.add_log_line)))
        self.add_awaitable(asyncio.create_task(
            close_pipe_on_exit(sox_proc, sox_write_fd)))

        ffmpeg_cmd = [
            self._ffmpeg_exec_path, '-i', '-', '-f', 'mp3', '-v', '24',
            self._icecast_url
        ]
        ffmpeg_proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdin=sox_read_fd, stderr=subprocess.PIPE)
        self.add_process_handle(ffmpeg_proc)
        self.add_awaitable(asyncio.create_task(
            read_lines_from_stream(ffmpeg_proc.stderr, self.add_log_line)))

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
