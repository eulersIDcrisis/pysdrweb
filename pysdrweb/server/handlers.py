"""handlers.py.

Implementation of the primary handlers for the API routes of the web radio.
These routes should work for most drivers out of the box and are generic.

These routes do _not_ handle the static files or other portions of the server,
however; this should be handled elsewhere.
"""
import os
import base64
import asyncio
from functools import wraps
from tornado import ioloop, httpserver, netutil, web
from pysdrweb.util.logger import logger
from pysdrweb.driver.common import UnsupportedFormatError


class NotAuthorized(Exception):
    """Exception denoting the caller is not authorized."""


class BaseRequestHandler(web.RequestHandler):

    def get_driver(self):
        return self.get_context().driver

    def get_context(self):
        return self.application.settings['context']

    def send_status(self, code, message):
        self.set_status(code)
        self.write(dict(status=code, message=message))


class FrequencyHandler(BaseRequestHandler):

    def _process_auth_header(self):
        context = self.get_context()
        # Check for the authentication header.
        header = self.request.headers.get('Authorization', None)
        if context.admin_user and context.admin_password:
            if not header:
                raise NotAuthorized()
            try:
                # Decode the header as a string.
                decoded = base64.b64decode(header).decode('utf-8')
                user, password = decoded.split(':', 1)
                if (user != context.admin_user and
                        password != context.admin_password):
                    raise Exception('Password did not match!')
            except Exception as exc:
                logger.error('Error decoding token: %s', exc)
                raise NotAuthorized()

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
            self._process_auth_header()
        except Exception:
            self.set_header('WWW-Authenticate', 'Basic realm="PySDRWeb"')
            self.send_status(401, 'Authentication required!')
            return
        try:
            new_freq = self.get_argument('frequency')
        except Exception:
            self.send_status(400, 'Bad arguments!')
            return

        try:
            driver = self.get_driver()
            await driver.change_frequency(new_freq)
            # Redirect back to the main page to refresh it, but stall so the
            # driver has a chance to start up.
            await asyncio.sleep(2.0)
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


async def _stream_audio(req_handler, driver, timeout, fmt):
    """Helper that streams  the audio."""
    try:
        # Disable the cache for these requests.
        req_handler.set_header('Cache-Control', 'no-cache')
        await driver.process_request(req_handler, fmt, timeout)
    except UnsupportedFormatError as exc:
        req_handler.send_status(400, str(exc))
    except Exception:
        logger.exception(
            "Error streaming audio! Driver: %s Format: %s", driver.name, fmt)
        req_handler.send_status(500, "Internal Server Error.")


class ProcessAudioHandler(BaseRequestHandler):
    """Handler that streams the audio in the requested format.

    This handler defers all of its calls to the local driver. The format
    requested is implicit in the extension. This supports the following
    query parameters:
     - timeout: How long (in seconds) to listen to the stream and encode
            the stream before finalizing the stream. If this is omitted
            (or is negative), then this continues indefinitely until the
            connection is broken.
     - download: If passed, this sets the 'Content-Disposition' header
            so that the file can be treated as a download by the browser.
    """

    async def get(self, ext):
        if not ext:
            ext = 'MP3'
        if ext.startswith('.'):
            ext = ext[1:]
        driver = self.get_driver()

        # Parse the timeout query parameter.
        try:
            timeout = self.get_argument('timeout', None)
            if timeout is not None:
                timeout = float(timeout)

            # The helper takes control of the request once called.
            await _stream_audio(self, driver, timeout, ext)
        except Exception:
            self.send_status(400, "Bad 'timeout' parameter!")


def get_api_routes():
    """Return the main API routes (for use with tornado.web.Application).

    NOTE: All of the routes here are prefixed with: '/api'
    """
    return [
        (r'/api/radio/audio(\.[a-zA-Z0-9]+)?', ProcessAudioHandler),
        (r'/api/frequency', FrequencyHandler),
        (r'/api/procinfo', ContextInfoHandler),
    ]


class Context(object):

    def __init__(self, driver, auth_dict):
        self.driver = driver
        self.auth_dict = auth_dict if auth_dict else {}

    @property
    def admin_user(self):
        return self.auth_dict.get('user', 'admin')

    @property
    def admin_password(self):
        return self.auth_dict.get('password')


class Server(object):

    def __init__(self, driver, auth_dict=None, port=None):
        # self.config = config
        self._context = Context(driver, auth_dict)
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
        await self._context.driver.start(frequency)
        # Register the driver to stop.
        async def _stop_driver():
            self._context.driver.stop()
            await self._context.driver.wait()
        self._drain_hooks.append(_stop_driver)

        # Now that the process is started, setup the server.
        routes = get_api_routes()
        routes.extend([
            (r'/', web.RedirectHandler, dict(url='/static/index.html')),
            (r'/static/(.*)', web.StaticFileHandler, dict(
                path=get_static_file_location()))
        ])
        app = web.Application(routes, context=self._context)

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
    file_path = os.path.join(dir_path, '..', 'static')
    return os.path.realpath(file_path)
