"""ioloop.py.

Utilities for managing the IOLoop in pysdrweb.
"""
import os
import signal
import asyncio
from functools import partial
from tornado import ioloop, httpserver, netutil
from pysdrweb.util.logger import logger


UNIX_PREFIX = 'unix:'


def _remove_unix_socket(path):
    try:
        os.remove(path)
    except OSError:
        pass


class IOLoopContext(object):
    """Primary context that manages an IOLoop.

    This class permits creating servers and running the applicable IOLoop.
    It also permits registering different callbacks during shutdown (shutdown
    hooks) as well as 'draining' callbacks (drain hooks) that can be
    registered and invoked when the IOLoop is requested to stop.
    """

    def __init__(self):
        self._shutdown_hooks = []
        self._drain_hooks = []

        # Store the IOLoop here for reference when shutting down.
        self._loop = ioloop.IOLoop.current()

    @property
    def ioloop(self):
        """Return the IOLoop this context manages."""
        return self._loop

    def add_shutdown_hook(self, fn, *args, **kwargs):
        """Register the given callback to run during shutdown.

        NOTE: These callbacks are executed in the reverse order they are added,
        in a manner similar to ExitStack().
        """
        if not callable(fn):
            raise TypeError("Given 'fn' is not callable!")
        self._shutdown_hooks.append(partial(fn, *args, **kwargs))

    def add_drain_hook(self, async_fn):
        """Add the given coroutine to run when stopping the server.

        NOTE: 'async_fn' must be a coroutine that can be awaited!
        """
        if not asyncio.iscoroutinefunction(async_fn):
            raise TypeError("Given 'async_fn' is not a coroutine!")
        self._drain_hooks.append(async_fn)

    def create_http_server(self, app, ports):
        """Create an HTTPServer instance that listens on the given ports.

        NOTE: If a string is passed for a UNIX socket, it should be prefixed
        with 'unix:' to identify it; otherwise an exception will be thrown.
        """
        server = httpserver.HTTPServer(app)
        for port in ports:
            if isinstance(port, int):
                sockets = netutil.bind_sockets(port)
                server.add_sockets(sockets)
            elif isinstance(port, str):
                if not port.startswith('unix:'):
                    raise ValueError(
                        "Invalid port (or UNIX socket path): {}".format(port))
                path = port[len(UNIX_PREFIX):]
                socket = netutil.bind_unix_socket(path)
                server.add_socket(socket)
                self.add_shutdown_hook(_remove_unix_socket, path)
            else:
                raise TypeError("Invalid port: {}".format(port))

        async def _drain_connections():
            """Helper that registers the server to drain connections."""
            server.stop()
            await server.close_all_connections()

        self.add_drain_hook(_drain_connections)

        return server

    def run(self, register_sighandler=True):
        """Run the IOLoop on the thread this method is called."""
        if register_sighandler:
            # Setup the signal handler.
            def _sighandler(signum, stack_frame):
                self.stop(from_signal_handler=True)
            signal.signal(signal.SIGINT, _sighandler)
            signal.signal(signal.SIGTERM, _sighandler)

        try:
            # Run the loop.
            self._loop.start()
            self._loop.close()
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
        """Schedule this IOLoopContext to stop.

        NOTE: This is thread-safe and can be called from anywhere. This call
        does NOT wait for the loop to stop.
        """
        logger.info('Server shutdown requested.')
        if from_signal_handler:
            self._loop.add_callback_from_signal(self._stop)
        else:
            self._loop.add_callback(self._stop)

    async def _stop(self):
        """Helper to stop the IOLoop by running the drain hooks."""
        logger.info("Running %d callbacks to drain the server.",
                    len(self._drain_hooks))
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
