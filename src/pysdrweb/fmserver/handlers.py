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
from pysdrweb.util.auth import authenticated
from pysdrweb.data import get_data_file_stream
from pysdrweb.fmserver import encoder, hls_streaming
from pysdrweb.fmserver.context import FmServerContext


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


class FrequencyHandler(FmRequestHandler):

    @authenticated(readonly=True)
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

    @authenticated(readonly=False)
    async def post(self):
        try:
            new_freq = self.get_argument('frequency')
        except Exception:
            self.send_status(400, 'Bad arguments!')
            return

        try:
            context = self.get_context()
            await context.change_frequency(new_freq)
            # Redirect back to the main page to refresh it, but stall so the
            # context has a chance to start up.
            await asyncio.sleep(2.0)
            self.redirect('/')
        except Exception:
            logger.exception('Error updating frequency!')
            self.send_status(500, 'Internal Server Error')


class ContextInfoHandler(FmRequestHandler):

    @authenticated(readonly=True)
    async def get(self):
        try:
            driver = self.get_driver()
            self.set_header('Content-Type', 'text/plain')
            self.write(driver.get_log())
        except Exception:
            logger.exception('Error fetching context/driver information!')
            self.set_status(500)
            self.write(dict(status=500, message="Internal Server Error"))


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
            The query parameter can include the 'name' of the file, if
            desired, but the extension will be enforced.
    """

    @authenticated(readonly=True)
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

        try:
            download_name = self.get_argument('download', None)
            # NOTE: 'download_name' could be the empty string, in which case
            # the caller wants the default filename.
            if download_name is not None:
                # download_name is empty.
                if not download_name:
                    download_name = 'audio.{}'.format(ext.lower())
                elif not download_name.upper().endswith(ext):
                    # If the file ends with the proper extension, we are good.
                    download_name = '{}.{}'.format(
                        download_name, ext.lower())
                # Use the resulting download_name to set the header
                self.set_header(
                    'Content-Disposition',
                    'attachment; filename={}'.format(download_name))
        except Exception as exc:
            # Ignore any exceptions here, since this only affects the
            # download characteristic of the file.
            logger.warning('Error parsing "download" parameter: %s', exc)

        driver = self.get_driver()
        if not driver.is_running():
            self.send_status(409, "Driver is not running!")
            return

        # Parse the timeout query parameter.
        try:
            timeout = self.get_argument('timeout', None)
            if timeout is not None:
                timeout = float(timeout)
        except Exception:
            self.send_status(400, "Bad 'timeout' parameter!")
            return

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


class IndexFileHandler(web.RequestHandler):

    def get(self):
        try:
            content = get_data_file_stream('index.html')
            self.set_header('Content-Type', 'text/html')
            self.write(content.read())
        except Exception:
            logger.exception('Content not found!')
            self.set_status(500)


def get_static_file_location():
    """Return the static files relative to this directory."""
    return os.path.realpath(os.path.join(
        os.path.dirname(__file__), 'static'
    ))


#
# Generate the Application
#
def generate_app(context, include_static_files=True):
    # Now that the process is started, setup the server.
    routes = [
        (r'/api/radio/audio(\.[a-zA-Z0-9]+)?',
            ProcessAudioHandler, dict(context=context)),
        (r'/api/frequency', FrequencyHandler, dict(context=context)),
        (r'/api/procinfo', ContextInfoHandler, dict(context=context)),
    ]
    if context.hls_manager:
        routes.extend(hls_streaming.get_hls_routes(
            context, prefix='/api/stream'
        ))
    if include_static_files:
        routes.extend([
            (r'/', web.RedirectHandler, dict(url='/static/index.html')),
            # TODO -- Create a more generic 'StaticFileHandler' that serves
            # content from pkg_resources or similar.
            (r'/static/index.html', IndexFileHandler),
            # (r'/static/(.*)', web.StaticFileHandler, dict(
            #     path=get_static_file_location()))
        ])
    return web.Application(routes)
