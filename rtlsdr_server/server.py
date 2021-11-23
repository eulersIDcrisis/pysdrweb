"""server.py.

Server with a simple API to stream to an icecast server.
"""
import os
import shlex
import signal
import logging
import asyncio
import argparse
import traceback
import subprocess
from urllib.parse import urlsplit, urlunsplit
from tornado import web, ioloop, httpclient, httpserver, netutil

from driver import (
    IcecastRtlFMDriver, UnsupportedFormatError,
)


logger = logging.getLogger()


class BaseRequestHandler(web.RequestHandler):

    def get_driver(self):
        return self.application.settings['driver']

    def send_status(self, code, message):
        self.write(dict(status=code, message=message))


class FrequencyHandler(BaseRequestHandler):

    async def get(self):
        try:
            driver = self.get_driver()
            self.write(dict(
                frequency=driver.frequency,
                running=bool(driver.is_running())
            ))
        except Exception:
            logger.exception('Error fetching frequency!')
            self.send_status(500, 'Internal Server Error')

    async def post(self):
        try:
            new_freq = self.get_argument('frequency')
            logger.info("PARSED FREQ: %s", new_freq)
        except Exception:
            self.send_status(400, 'Bad arguments!')
            return

        try:
            driver = self.get_driver()
            logger.info("STARTING to change frequency")
            await driver.change_frequency(new_freq)
            logger.info("done changing frequency")
            # Redirect back to the main page to refresh it.
            self.redirect('/')
        except Exception:
            logger.exception('Error updating frequency!')
            self.send_status(500, 'Internal Server Error')


class ContextInfoHandler(BaseRequestHandler):

    async def get(self):
        try:
            driver = self.get_driver()
            self.set_header('Content-Type', 'text/plain')
            self.write(driver.get_log())
        except Exception:
            logger.exception('Error fetching context/driver information!')
            self.set_status(500)
            self.write(dict(status=500, message="Internal Server Error"))


class IcecastProxyHandler(BaseRequestHandler):

    async def get(self, ext):
        if not ext:
            ext = 'mp3'

        logger.info("Using extension: %s", ext)
        driver = self.get_driver()
        # The driver takes total control of the request at this point.
        await driver.process_request(self, ext)


class ProcessAudioHandler(BaseRequestHandler):

    async def get(self, ext):
        if not ext:
            ext = 'mp3'
        driver = self.get_driver()
        try:
            await driver.process_request(self, ext)
        except UnsupportedFormatError as exc:
            self.send_status(400, str(exc))
        except Exception:
            logger.exception("Error in process audio handler!")
            self.send_status(500, "Internal Server Error.")


class Server(object):

    def __init__(self, driver, port=None):
        # self.config = config
        self._driver = driver
        self._port = port or 8000

        self._shutdown_hooks = []
        self._drain_hooks = []

        # Store the IOLoop here for reference when shutting down.
        self._loop = None

    def run(self):
        self._loop = ioloop.IOLoop.current()
        try:
            self._loop.add_callback(self._start)

            # Run the loop.
            self._loop.start()
        except Exception:
            logger.exception("Error in IOLoop!")
        # Run the shutdown hooks.
        logger.info("Running %d shutdown hooks.", len(self._shutdown_hooks))
        for hook in reversed(self._shutdown_hooks):
            try:
                hook()
            except Exception:
                logger.exception('Failed to run shutdown hook!')
        logger.info("Server should be stopped.")

    def stop(self, from_signal_handler=False):
        logger.info('Server shutdown requested.')
        if from_signal_handler:
            self._loop.add_callback_from_signal(self._stop)
        else:
            self._loop.add_callback(self._stop)

    async def _start(self):
        # Start the driver.
        frequency = '107.3M'
        logger.info('Starting on frequency: %s', frequency)
        await self._driver.start(frequency)
        # Register the driver to stop.
        async def _stop_driver():
            self._driver.stop()
            await self._driver.wait()
        self._drain_hooks.append(_stop_driver)

        # Now that the process is started, setup the server.
        app = web.Application([
            (r'/', web.RedirectHandler, dict(url='/static/index.html')),
            (r'/radio(\.[a-zA-Z0-9]+)?', ProcessAudioHandler),
            (r'/api/frequency', FrequencyHandler),
            (r'/api/procinfo', ContextInfoHandler),
            (r'/static/(.*)', web.StaticFileHandler, dict(
                path=get_static_file_location()))
        ], driver=self._driver)

        logger.info('Running server on port: %d', self._port)
        sockets = netutil.bind_sockets(self._port)
        server = httpserver.HTTPServer(app)
        server.add_sockets(sockets)

        async def _close_server():
            server.stop()
            await server.close_all_connections()
        self._drain_hooks.append(_close_server)

    async def _stop(self):
        # Run the drain hooks in reverse.
        timeout = 5
        for hook in reversed(self._drain_hooks):
            try:
                await asyncio.wait_for(hook(), timeout)
            except asyncio.TimeoutError:
                logger.warning('Drain hook timed out after %d seconds.',
                               timeout)
            except Exception:
                logger.exception('Error running drain hook!')
        # Stop the current loop.
        ioloop.IOLoop.current().stop()


def get_static_file_location():
    dir_path = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(dir_path, 'static')


def find_executable(cmd):
    cmd = shlex.join(['which', cmd])
    proc = subprocess.run(cmd, shell=True, capture_output=True)
    return proc.stdout.decode('utf-8').strip()


def run():
    parser = argparse.ArgumentParser(
        description="Run an web server to play FM Radio using RTL-SDR.")
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity.", default=0)
    parser.add_argument('-p', '--port', type=int, help="Port to listen on.")
    parser.add_argument('--rtl-fm', type=str, default=None,
                        help="Path to the 'rtl_fm' executable.")
    parser.add_argument('--sox', type=str, default=None,
                        help="Path to the 'sox' executable.")
    parser.add_argument('--ffmpeg', type=str, default=None,
                        help="Path to the 'ffmpeg' executable.")
    parser.add_argument(
        '--icecast-url', type=str, help=(
        "Icecast URL, including user/password field."),
        default="icecast://source:hackme@localhost:8000/radio")

    args = parser.parse_args()

    if args.verbose > 0:
        level = logging.DEBUG
    else:
        level = logging.INFO

    # Setup logging.
    logging.basicConfig(level=level)

    # Set the port.
    port = args.port

    # Parse the executable paths.
    if args.rtl_fm:
        rtl_path = args.rtl_fm
    else:
        rtl_path = find_executable('rtl_fm')

    if args.sox:
        sox_path = args.sox
    else:
        sox_path = find_executable('sox')

    if args.ffmpeg:
        ffmpeg_path = args.ffmpeg
    else:
        ffmpeg_path = find_executable('ffmpeg')

    # Parse the icecast URL
    _, netloc, path, query, fragment = urlsplit(args.icecast_url)
    parts = netloc.split('@')
    if len(parts) > 1:
        netloc = parts[1]
    client_url = urlunsplit(('http', netloc, path, query, fragment))
    config = dict(
        rtl_fm=rtl_path, sox=sox_path, ffmpeg=ffmpeg_path,
        icecast_url=args.icecast_url, client_url=client_url
    )
    driver = IcecastRtlFMDriver(config)

    server = Server(driver, port=port)
    def _sighandler(signum, stack_frame):
        server.stop(from_signal_handler=True)

    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    server.run()


if __name__ == '__main__':
    run()
