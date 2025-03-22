"""event_loop_util.py.

Utilities for managing the IOLoop in pysdrweb.
"""

# Typing Imports
from typing import Union, Any
from collections.abc import Callable, Awaitable

# Standard Imports
import os
import signal
import asyncio
from functools import partial

# Third-party Imports
from tornado import httpserver, netutil, web

# Local Imports
from pysdrweb.util.logger import logger


UNIX_PREFIX = "unix:"


def _remove_unix_socket(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


class EventLoopContext:
    """Primary context that manages an IOLoop.

    This class permits creating servers and running the applicable IOLoop.
    It also permits registering different callbacks during shutdown (shutdown
    hooks) as well as 'draining' callbacks (drain hooks) that can be
    registered and invoked when the IOLoop is requested to stop.
    """

    def __init__(self):
        self._shutdown_hooks: list[Callable[[], Any]] = []
        self._drain_hooks: list[Callable[[], Union[None, Awaitable]]] = []

        # Store the IOLoop here for reference when shutting down.
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._initialized: bool = False

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop:
        """Return the IOLoop this context manages."""
        return self._loop

    async def initialize(self):
        if self._initialized:
            raise RuntimeError("initialize() already called.")
        self._initialized = True

    def add_shutdown_hook(self, fn: Callable, *args, **kwargs):
        """Register the given callback to run during shutdown.

        NOTE: These callbacks are executed in the reverse order they are added,
        in a manner similar to ExitStack().
        """
        if not callable(fn):
            raise TypeError("Given 'fn' is not callable!")
        self._shutdown_hooks.append(partial(fn, *args, **kwargs))

    def add_drain_hook(self, async_fn: Callable[[], Union[None, Awaitable]]):
        """Add the given coroutine to run when stopping the server.

        NOTE: 'async_fn' must be a coroutine that can be awaited!
        """
        if not callable(async_fn):
            raise TypeError("Given 'async_fn' is not callable!")
        self._drain_hooks.append(async_fn)

    def create_http_server(self, app: web.Application, ports: list[Union[str, int]]):
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
                if not port.startswith("unix:"):
                    raise ValueError(f"Invalid port (or UNIX socket path): {port}")
                path = port[len(UNIX_PREFIX) :]
                socket = netutil.bind_unix_socket(path)
                server.add_socket(socket)
                self.add_shutdown_hook(_remove_unix_socket, path)
            else:
                raise TypeError(f"Invalid port: {port}")

        async def _drain_connections():
            """Helper that registers the server to drain connections."""
            server.stop()
            await server.close_all_connections()

        self.add_drain_hook(_drain_connections)

        return server

    def run(self, register_sighandler: bool = True):
        """Run the IOLoop on the thread this method is called."""
        # Install 'self._loop' as the main event loop for this thread.
        asyncio.set_event_loop(self._loop)

        if register_sighandler:
            self._loop.add_signal_handler(signal.SIGINT, self.stop)
            self.add_shutdown_hook(self._loop.remove_signal_handler, signal.SIGINT)
            self._loop.add_signal_handler(signal.SIGTERM, self.stop)
            self.add_shutdown_hook(self._loop.remove_signal_handler, signal.SIGTERM)

        try:
            # Queue the request to initialize the context, if it hasn't been
            # initialized already.
            if not self._initialized:
                self._loop.run_until_complete(self.initialize())
            # Run the loop.
            self._loop.run_forever()
        except Exception:
            logger.exception("Error running the event loop!")
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.run_until_complete(self._loop.shutdown_default_executor())
            # Clear the drain hooks.
            self._drain_hooks = []

        # Run the shutdown hooks.
        logger.info("Running %d shutdown hooks.", len(self._shutdown_hooks))
        for hook in reversed(self._shutdown_hooks):
            try:
                hook()
            except Exception:
                logger.exception("Failed to run shutdown hook!")
        # Clear the shutdown hooks.
        self._shutdown_hooks = []
        logger.info("Stopped the server.")

    def stop(self):
        """Schedule this IOLoopContext to stop.

        NOTE: This is thread-safe and can be called from anywhere. This call
        does NOT wait for the loop to stop.
        """
        logger.info("Server shutdown requested.")
        asyncio.run_coroutine_threadsafe(self._stop(), self._loop)

    async def _stop(self):
        """Helper to stop the event loop by running the drain hooks."""
        logger.info("Running %d callbacks to drain the server.", len(self._drain_hooks))
        # Run the drain hooks in reverse.
        timeout = 5
        for hook in reversed(self._drain_hooks):
            try:
                res = hook()
                if asyncio.iscoroutine(res):
                    await asyncio.wait_for(res, timeout)
            except asyncio.TimeoutError:
                logger.warning("Drain hook timed out after %d seconds.", timeout)
            except Exception:
                logger.exception("Error running drain hook!")
        # Stop the current loop.
        self._loop.stop()
