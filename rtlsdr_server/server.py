"""server.py.

Server with a simple API to stream to an icecast server.
"""
import signal
import asyncio
import traceback
from tornado import web, ioloop, httpclient, httpserver, netutil

from driver import IcecastRtlFMDriver


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
            traceback.print_exc()
            self.send_status(500, 'Internal Server Error')

    async def post(self):
        try:
            driver = self.get_driver()
            await driver.change_frequency('107.3M')
        except Exception:
            traceback.print_exc()
            self.send_status(500, 'Internal Server Error')


class ContextInfoHandler(BaseRequestHandler):

    async def get(self):
        try:
            driver = self.get_driver()
            self.set_header('Content-Type', 'text/plain')
            self.write(driver.get_log())
        except Exception:
            traceback.print_exc()
            self.set_status(500)
            self.write(dict(status=500, message="Internal Server Error"))


class IcecastProxyHandler(BaseRequestHandler):

    async def data_received(self, chunk):
        self._send_queue.put(chunk)

    async def body_producer(self, write):
        while True:
            chunk = await self._send_queue.get()
            if chunk is None:
                return
            await write(chunk)

    async def get(self):
        # Proxy this request to the icecast server. This currently only
        # supports GET, since that is all that should be necessary.
        icecast_url = 'http://localhost:9000/serdsver.py'
        try:
            client = await httpclient.AsyncHTTPClient().fetch(
                httpclient.HTTPRequest(
                    icecast_url, streaming_callback=self.write)
            )
        except httpclient.HTTPError as exc:
            self.set_status(exc.code)
            # Write the headers.
            for name, header in exc.response.headers.items():
                if name.upper() in ['SERVER']:
                    continue
                self.set_header(name, header)
            # Write the response.
            self.write(exc.response.body)
        except Exception:
            self.set_status(500)
            self.write(dict(status=500, message="Internal Server Error."))
            traceback.print_exc()


class Server(object):

    def __init__(self, driver, port=None):
        # self.config = config
        self._driver = driver
        self._port = port or 8000

        self._shutdown_hooks = []
        self._drain_hooks = []

        self._ioloop = None

        # Create the RtlFMDriver
        # self._context = RtlFMDriver()

    def run(self):
        self._ioloop = ioloop.IOLoop.current()
        try:
            self._ioloop.add_callback(self._start)

            # Run the loop.
            self._ioloop.start()
        except Exception:
            traceback.print_exc()
        # Run the shutdown hooks.
        for hook in reversed(self._shutdown_hooks):
            try:
                hook()
            except Exception:
                traceback.print_exc()

    def stop(self, from_signal_handler=False):
        if from_signal_handler:
            self._ioloop.add_callback_from_signal(self._stop)
        else:
            self._ioloop.add_callback(self._stop)

    async def _start(self):
        # Start the driver.
        await self._driver.start('107.3M')
        # await self._context.start()

        # Now that the process is started, setup the server.
        app = web.Application([
            (r'/', web.RedirectHandler, dict(url='/static/index.html')),
            (r'/radio', IcecastProxyHandler),
            (r'/api/frequency', FrequencyHandler),
            (r'/api/procinfo', ContextInfoHandler),
            (r'/static/(.*)', web.StaticFileHandler)
        ], driver=self._driver)
        sockets = netutil.bind_sockets(self._port)
        server = httpserver.HTTPServer(app)
        server.add_sockets(sockets)

        async def _close_server():
            server.stop()
            await server.close_all_connections()
        self._drain_hooks.append(_close_server)

    async def _stop(self):
        # Run the drain hooks in reverse.
        # TODO: Run with some timeout
        for hook in reversed(self._drain_hooks):
            try:
                await asyncio.wait_for(hook(), 5)
            except asyncio.TimeoutError:
                print("Drain hook timed out.")
            except Exception:
                traceback.print_exc()
        # Stop the current loop.
        ioloop.IOLoop.current().stop()


def run():
    driver = IcecastRtlFMDriver(dict())
    server = Server(driver)

    def _sighandler(signum, stack_frame):
        server.stop(from_signal_handler=True)

    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    server.run()


if __name__ == '__main__':
    run()
