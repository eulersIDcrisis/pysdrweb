"""server.py.

Server utilities for the FM server mode for the RTL-SDR dongle.
"""
import os


def get_static_file_location():
    """Return the static files for the FM Server."""
    dir_path = os.path.dirname(os.path.realpath(__file__))
    file_path = os.path.join(dir_path, '..', 'static')
    return os.path.realpath(file_path)


class FMServerContext(object):
    """Context for the FM Server part of this RTL-SDR server."""

    def __init__(self, driver, auth_dict):
        self._driver = driver
        self._start_frequency = '107.3M'
        if auth_dict:
            auth_type = auth_dict.get('type', 'none').lower()
            if auth_type not in set(['basic', 'none', 'null']):
                raise ValueError(
                    'Unsupported auth type: {}'.format(auth_type))
            self.auth_dict = auth_dict
        else:
            self.auth_dict = {}

    @property
    def driver(self):
        """Get the primary driver for this context."""
        return self._driver

    @property
    def admin_user(self):
        return self.auth_dict.get('user', 'admin')

    @property
    def admin_password(self):
        return self.auth_dict.get('password')

    def get_routes(self):
        routes = get_api_routes()
        routes.extend([
            (r'/', web.RedirectHandler, dict(url='/static/index.html')),
            (r'/static/(.*)', web.StaticFileHandler, dict(
                path=get_static_file_location()))
        ])
        return routes

    async def start(self):
        # Start the driver.
        logger.info('Starting on frequency: %s', frequency)
        await self.driver.start(frequency)


class Server(object):

    def __init__(self, context, port=None):
        # self.config = config
        self._context = context
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
