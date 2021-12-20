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
from tornado import ioloop, iostream, httpserver, netutil, web
from pysdrweb.util.logger import logger
from pysdrweb.fmserver import encoder


class NotAuthorized(Exception):
    """Exception denoting the caller is not authorized."""



def get_static_file_location():
    return os.path.realpath(os.path.join(
        os.path.dirname(__file__), 'static'
    ))


class FmServerContext(object):

    def __init__(self, driver, auth_dict, options=None):
        self._driver = driver
        self._auth_dict = auth_dict if auth_dict else {}
        options = options if options else {}
        self._default_frequency = options.get('default_frequency', '107.3M')

    @property
    def driver(self):
        """Return the driver for this context."""
        return self._driver

    async def start(self):
        # Start the driver.
        logger.info('Starting on frequency: %s', self._default_frequency)
        await self.driver.start(self._default_frequency)

    async def stop(self):
        self.driver.stop()
        await self.driver.wait()

    def generate_app(self, include_static_files=True):
        # Now that the process is started, setup the server.
        routes = [
            (r'/api/radio/audio(\.[a-zA-Z0-9]+)?',
                ProcessAudioHandler, dict(context=self)),
            (r'/api/frequency', FrequencyHandler, dict(context=self)),
            (r'/api/procinfo', ContextInfoHandler, dict(context=self)),
        ]
        if include_static_files:
            routes.extend([
                (r'/', web.RedirectHandler, dict(url='/static/index.html')),
                (r'/static/(.*)', web.StaticFileHandler, dict(
                    path=get_static_file_location()))
            ])
        return web.Application(routes)


class FmRequestHandler(web.RequestHandler):

    def initialize(self, context=None):
        """Initialize the handler with the current context."""
        self._context = context

    def get_driver(self):
        """Get the FM driver for the handler."""
        return self.get_context().driver

    def get_context(self):
        """Get the current FMServerContext for the handler."""
        return self._context

    def send_status(self, code, message):
        """Helper to send a JSON message for the given status."""
        self.set_status(code)
        self.write(dict(status=code, message=message))

    def _process_auth_header(self):
        context = self.get_context()
        # Check for the authentication header.
        header = self.request.headers.get('Authorization', None)
        if context.auth_type == 'basic':
            if not header:
                raise NotAuthorized()
            HEADER_PREFIX = 'Basic '
            if not header.startswith(HEADER_PREFIX):
                raise NotAuthorized()
            try:
                # Decode the header as a string.
                decoded = base64.b64decode(
                    # Drop the 'Basic ' portion of the header.
                    header[len(HEADER_PREFIX):]
                ).decode('utf-8')
                user, password = decoded.split(':', 1)
                if (user != context.admin_user or
                        password != context.admin_password):
                    raise Exception('Password did not match!')
            except Exception as exc:
                logger.error('Error decoding token: %s', exc)
                raise NotAuthorized()


class FrequencyHandler(FmRequestHandler):

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
            self.set_header('WWW-Authenticate', 'Basic realm="FM SDR Server"')
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


class ContextInfoHandler(FmRequestHandler):

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
    """Helper that streams the audio."""
    try:
        # Disable the cache for these requests.
        req_handler.set_header('Cache-Control', 'no-cache')
        await driver.process_request(req_handler, fmt, timeout)
    except encoder.UnsupportedFormatError as exc:
        req_handler.send_status(400, str(exc))
    except Exception:
        logger.exception(
            "Error streaming audio! Driver: %s Format: %s", driver.name, fmt)
        req_handler.send_status(500, "Internal Server Error.")


class ProcessAudioHandler(FmRequestHandler):
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
        try:
            if not ext:
                ext = 'MP3'
            if ext.startswith('.'):
                ext = ext[1:]
            # Make sure this is upper case.
            ext = ext.upper()
        except Exception:
            self.send_status(400, 'Bad format!')
            return

        driver = self.get_driver()

        # Parse the timeout query parameter.
        try:
            timeout = self.get_argument('timeout', None)
            if timeout is not None:
                timeout = float(timeout)
        except Exception:
            self.send_status(400, "Bad 'timeout' parameter!")

        # Now, try and encode the request. Any exceptions at this point are
        # either 'UnsupportedFormatError' which maps to a 400 code, or
        # anything else, which maps to a 500 code, or just stops the request
        # in transit (i.e. because the data has already started streaming).
        try:
            content_type = encoder.get_mime_type_for_format(ext)
            file_obj = encoder.RequestFileHandle(self)
            # Start encoding.
            async def _flush():
                await self.flush()
            await encoder.encode_from_driver(
                driver, file_obj, ext, timeout, async_flush=_flush)
        except encoder.UnsupportedFormatError as exc:
            self.send_status(404, str(exc))
        except iostream.StreamClosedError:
            # Expected. Exit cleanly.
            return
        except Exception:
            logger.exception('Error encoding PCM data!')
            # Attempt to send the error.
            self.send_status(500, 'Internal Server Error')
