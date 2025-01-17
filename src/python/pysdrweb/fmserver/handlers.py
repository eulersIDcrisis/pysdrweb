"""handlers.py.

Implementation of the primary handlers for the API routes of the web radio.
These routes should work for most drivers out of the box and are generic.

These routes do _not_ handle the static files or other portions of the server,
however; this should be handled elsewhere.
"""

import os
import asyncio
from contextlib import ExitStack
from tornado import iostream, web
from pysdrweb.util.logger import logger
from pysdrweb.util.auth import authenticated
from pysdrweb.encoders import (
    get_encoder_for_format_type,
    get_mime_type_for_format,
    UnsupportedFormatError,
)
from pysdrweb.drivers import AbstractPCMDriver
from pysdrweb.fmserver import hls_streaming

try:
    # Import the static template files.
    from pysdrweb_data import get_data_file_stream

    _STATIC_FILES_BUNDLED = True
except ImportError:
    _STATIC_FILES_BUNDLED = False


class RequestFileHandle:
    """Wrapper class to give 'web.RequestHandler' a 'file-like' interface.

    This fakes a seekable stream to avoid exceptions when using this with
    python's "wave" and "aifc" modules.
    """

    def __init__(self, handler: web.RequestHandler):
        """Constructor that accepts the RequestHandler to write to."""
        self._handler = handler
        self._count = 0

    def write(self, data):
        """Write the given data to the handler."""
        self._handler.write(data)
        self._count += len(data)

    def close(self):
        """No-op when wrapping some RequestHandler instance."""

    def tell(self):
        """Return current bytes written.

        The number of bytes written is also the current position in the
        stream, since we do not permit seeking.
        """
        return self._count

    def seek(self, *args):
        """No-op, since this stream is not seekable."""

    def flush(self):
        """Flush the data to the stream."""

    async def async_flush(self):
        """Asynchronously flush the data to the stream."""
        await self._handler.flush()


class FmRequestHandler(web.RequestHandler):
    """Base handler with common access to the AbstractPCMDriver."""

    def initialize(self, context=None):
        """Initialize the handler with the current context."""
        self._context = context

    def get_driver(self) -> AbstractPCMDriver:
        """Get the FM driver for the handler."""
        return self.get_context().driver

    def get_context(self):
        """Get the current FMServerContext for the handler."""
        return self._context

    def send_status(self, code: int, message: str):
        """Helper to send a JSON message for the given status."""
        self.set_status(code)
        self.write({"status": code, "message": message})


class FrequencyHandler(FmRequestHandler):
    """Handler to fetch and change the frequency for the driver."""

    @authenticated(readonly=True)
    async def get(self):
        try:
            driver = self.get_driver()
            self.write(
                {"frequency": driver.frequency, "running": bool(driver.is_running())}
            )
        except Exception:
            logger.exception("Error fetching frequency!")
            self.send_status(500, "Internal Server Error")

    @authenticated(readonly=False)
    async def post(self):
        try:
            new_freq = self.get_argument("frequency")
        except Exception:
            self.send_status(400, "Bad arguments!")
            return

        try:
            context = self.get_context()
            await context.change_frequency(new_freq)
            # Redirect back to the main page to refresh it, but stall so the
            # context has a chance to start up.
            await asyncio.sleep(2.0)
            self.redirect("/")
        except Exception:
            logger.exception("Error updating frequency!")
            self.send_status(500, "Internal Server Error")


class ContextInfoHandler(FmRequestHandler):

    @authenticated(readonly=True)
    async def get(self):
        try:
            driver = self.get_driver()
            self.set_header("Content-Type", "text/plain")
            self.write(driver.get_log())
        except Exception:
            logger.exception("Error fetching context/driver information!")
            self.set_status(500)
            self.write({"status": 500, "message": "Internal Server Error"})


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
                ext = "MP3"
            if ext.startswith("."):
                ext = ext[1:]
            # Make sure this is upper case.
            ext = ext.upper()
        except Exception:
            self.send_status(400, "Bad format!")
            return

        try:
            download_name = self.get_argument("download", None)
            # NOTE: 'download_name' could be the empty string, in which case
            # the caller wants the default filename.
            if download_name is not None:
                # download_name is empty.
                if not download_name:
                    download_name = f"audio.{ext.lower()}"
                elif not download_name.upper().endswith(ext):
                    # If the file ends with the proper extension, we are good.
                    download_name = f"{download_name}.{ext.lower()}"
                # Use the resulting download_name to set the header
                self.set_header(
                    "Content-Disposition",
                    f"attachment; filename={download_name}",
                )
        except Exception as exc:
            # Ignore any exceptions here, since this only affects the
            # download characteristic of the file.
            logger.warning("Error parsing 'download' parameter: %s", exc)

        driver = self.get_driver()
        if not driver.is_running():
            self.send_status(409, "Driver is not running!")
            return

        # Parse the timeout query parameter.
        try:
            timeout = self.get_argument("timeout", None)
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
            content_type = get_mime_type_for_format(ext)
            self.set_header("Content-Type", content_type)
            file_obj = RequestFileHandle(self)

            # Start encoding.
            async def _flush():
                await self.flush()

            encoder = get_encoder_for_format_type(driver, ext)
            await encoder.encode(file_obj, ext, timeout, async_flush=_flush)

        except UnsupportedFormatError as exc:
            self.send_status(404, str(exc))
        except iostream.StreamClosedError:
            # Expected. Exit cleanly.
            return
        except Exception:
            logger.exception("Error encoding PCM data!")
            # Attempt to send the error.
            self.send_status(500, "Internal Server Error")


if _STATIC_FILES_BUNDLED:

    class IndexFileHandler(web.RequestHandler):
        """Handler that serves static files."""

        async def get(self):
            with ExitStack() as exit_stack:
                try:
                    content = get_data_file_stream("index.html")
                    exit_stack.callback(content.close)
                    self.set_header("Content-Type", "text/html")
                    self.write(content.read())
                except Exception:
                    logger.exception("Content not found!")
                    self.set_status(404)
                    self.write({"status": 404, "message": "Content not found!"})

    def get_static_file_location():
        """Return the static files relative to this directory."""
        return os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))


#
# Generate the Application
#
def generate_app(context, include_static_files=True):
    # Now that the process is started, setup the server.
    context_args = {"context": context}
    routes = [
        (
            r"/api/radio/audio(\.[a-zA-Z0-9]+)?",
            ProcessAudioHandler,
            context_args,
        ),
        (r"/api/frequency", FrequencyHandler, context_args),
        (r"/api/procinfo", ContextInfoHandler, context_args),
    ]
    if context.hls_manager:
        routes.extend(hls_streaming.get_hls_routes(context, prefix="/api/stream"))
    if include_static_files and _STATIC_FILES_BUNDLED:
        routes.extend(
            [
                (r"/", web.RedirectHandler, {"url": "/static/index.html"}),
                # TODO -- Create a more generic 'StaticFileHandler' that serves
                # content from pkg_resources or similar.
                (r"/static/index.html", IndexFileHandler),
                # (r'/static/(.*)', web.StaticFileHandler, dict(
                #     path=get_static_file_location()))
            ]
        )
    return web.Application(routes)
