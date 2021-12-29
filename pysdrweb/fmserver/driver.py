"""driver.py.

Drivers for the FMServerContext
"""
import io
import shlex
import asyncio
import subprocess
from collections import deque, OrderedDict

# Local imports.
from pysdrweb.util.logger import get_child_logger
from pysdrweb.util import misc
from pysdrweb.fmserver import encoder


logger = get_child_logger('fmdriver')


class AbstractRtlDriver(object):
    """Abstract driver that provides PCM data asynchronously."""

    def __init__(self, nchannels=1, framerate=44100, sample_width=2,
                 max_chunk_count=None, seq_index=0):
        """Create the RtlDriver.

        'max_chunk_count' configures the maximum number of chunks to store,
        as each chunk would be added by "add_chunk()".
        """
        self._processes = []
        self._futures = []
        self._frequency = '107.3M'
        self._log = deque()

        # Data buffer and indexing.
        self._buffer = deque(maxlen=max_chunk_count)
        self._seq_index = seq_index
        # Buffer queues and related keys.
        self._buffer_cond = asyncio.Condition()
        self._buffer_queues = dict()
        self._next_qid = 1
        self._stop_requested = asyncio.Event()
        # PCM Metadata
        self._nchannels = nchannels
        self._framerate = framerate
        self._sample_width = sample_width

    @property
    def framerate(self):
        """Sampling rate of the PCM data (in Hz)."""
        return self._framerate

    @property
    def sample_rate(self):
        """The sampling rate of the PCM data (in Hz). Alias of framerate."""
        return self.framerate

    @property
    def sample_width(self):
        """The width of each sample (in bytes)."""
        return self._sample_width

    @property
    def nchannels(self):
        """Number of channels for each sample."""
        return self._nchannels

    @property
    def framesize(self):
        """Number of bytes per single frame/sample of PCM data."""
        return self._nchannels * self._sample_width

    @property
    def frequency(self):
        """Return the current frequency this driver is configured for."""
        return self._frequency

    @property
    def stop_event(self):
        """Return an Event when this driver stops."""
        return self._stop_requested

    def is_running(self):
        """Return whether this is actually running or not."""
        for proc in self._processes:
            if proc.returncode is None:
                return True
        return False

    def get_log(self):
        """Return the (stderr) log from any subprocesses."""
        return ''.join(self._log)

    def add_log_line(self, line):
        """Add the given line to the log (as parsed from a subprocess)."""
        self._log.append(line)

    def add_process_handle(self, proc_handle):
        """Add a process handle to manage for the driver."""
        self._processes.append(proc_handle)
        # Also await the process in a future.
        self._futures.append(asyncio.create_task(proc_handle.wait()))

    def add_awaitable(self, fut):
        """Add an 'asyncio.Task' or similar to await on before exiting.

        These awaitables are awaited when a stop is requested for this driver.
        """
        self._futures.append(fut)

    async def start(self, frequency):
        raise NotImplementedError()

    def stop(self):
        """Request a stop for this current driver.

        This only requests that the driver stops; await on: 'self.wait()'
        to actually wait for the driver to stop.
        """
        # Set this now to prevent data from being queued and to signal that
        # this driver is draining.
        self._stop_requested.set()

        # Kill the processes registered so they exit.
        for proc in self._processes:
            if proc.returncode is None:
                proc.kill()

        # After configuring the processes to kill, queue up the request to
        # drain any callers that are iterating over the PCM data.
        for queue in self._buffer_queues.values():
            queue.put_nowait(None)

    async def wait(self):
        """Wait for this driver to fully stop."""
        if self._futures:
            await asyncio.gather(*self._futures)

    async def reset(self):
        """Reset the driver, by stopping (and waiting) for it to stop.

        After stopping the driver, this should cleanup any internal
        resources so that the driver is safe to 'start()' again.
        """
        if self.is_running():
            self.stop()
            await self.wait()
        self._futures = []
        self._processes = []
        self._log.clear()
        # Reset the stop events and the async queue
        self._stop_requested.clear()
        self._buffer_queues = dict()

    async def change_frequency(self, frequency):
        """Change the frequency this driver listens on.

        This resets the driver by stopping any current processes and
        restarting them for the new frequency (after clearing buffers, etc.)
        """
        # Reset the driver first, before starting it up again.
        await self.reset()

        # At this point, the process is stopped, so update the frequency.
        self._frequency = frequency

        # Start the process up again.
        await self.start(self._frequency)

    async def add_pcm_chunk(self, chunk):
        """Add the given chunk of data into the buffer.

        Also queues this data for anyone iterating over the PCM data.
        """
        seq_index = self._seq_index
        self._seq_index += 1
        item = misc.PCMBufferItem(seq_index, chunk)
        self._buffer.append(item)
        async with self._buffer_cond:
            for queue in self._buffer_queues.values():
                queue.put_nowait(item)

    async def pcm_item_generator(self, seq_index=-1):
        """Generator to iterate over the PCM data received.

        This will keep returning PCM data in chunks, along with a timestamp to
        monitor which chunk is currently available. This also adds data that
        already exists in the buffer to the current queue.

        This will stop iterating when a stop (or frequency change or similar)
        is requested.

        Parameters
        ----------
        seq_index: int or None
            The 'seq_index' to start iterating from. 'None' implies the very start.

        Yields
        ------
        PCMBufferItem: Data as it is ready.
        """
        if self._stop_requested.is_set():
            return
        qid = self._next_qid
        # Prevent unbounded growth of the key.
        if self._next_qid >= 4294967295:  # 2 ^ 32 - 1
            self._next_qid = 1
        else:
            self._next_qid += 1

        try:
            # TODO -- We could cap the number of elements in this queue,
            # which effectively restricts the size of the buffer for each
            # connection, but not urgent for now.
            iter_queue = asyncio.Queue()

            # Preload the queue with all of the data currently in the deque.
            for item in self._buffer:
                # Skip any data before the requested start address.
                if seq_index < item.seq_index:
                    iter_queue.put_nowait(item)

            # Add this buffer to receive new data from the handler as it is
            # received. This is protected by a condition variable, so acquire
            # that first.
            async with self._buffer_cond:
                self._buffer_queues[qid] = iter_queue
                self._buffer_cond.notify_all()

            # Now, consume data from the queue.
            while True:
                # If 'None' is parsed out from the queue, this is our cue (ha)
                # to exit.
                item = await iter_queue.get()
                if item is None:
                    return
                yield item
        finally:
            async with self._buffer_cond:
                self._buffer_queues.pop(qid, None)
                self._buffer_cond.notify_all()

    async def _wait_to_join_queues(self):
        """Wait for all of the queues to drain before exiting.

        Useful for subclasses and the like to drain quietly. This should
        usually be added via 'add_awaitable()' inside a subclass's version
        of 'async def start()'
        """
        # First, wait for the stop event. We don't want to wait for the queues
        # until a stop is actually requested.
        await self._stop_requested.wait()
        # Push 'None' into every queue; this signals to stop iterating.
        for queue in self._buffer_queues.values():
            queue.put_nowait(None)

        # Drain and wait for the size of the buffer_queues mapping to empty.
        async with self._buffer_cond:
            while len(self._buffer_queues) > 0:
                await self._buffer_cond.wait()


class RtlFmExecDriver(AbstractRtlDriver):
    """Driver that runs rtl_fm directly, then encodes the data on request.

    This driver basically runs:
        rtl_fm -f ${frequency} -s 200k -r 48k -A fast -

    then catches the input into a sliding window buffer. Then, the data
    will be encoded upon request into WAV, AIFF, or other formats.
    """

    @classmethod
    def from_config(cls, config):
        rtlfm = config.get('rtl_fm')
        if not rtlfm:
            rtlfm = misc.find_executable('rtl_fm')
            if not rtlfm:
                raise Exception("Could not find path to: rtl_fm")
            config['rtl_fm'] = rtlfm
        return cls(config)

    def __init__(self, config):
        # TODO -- Probably should not assume this path, but it works for now.
        self._rtlfm_exec_path = config['rtl_fm']
        framerate = config.get('framerate', 48000)

        # Extract the buffer size.
        kb_buffer_size = int(config.get('kb_buffer_size', 128))
        super(RtlFmExecDriver, self).__init__(
            # RTL-FM dumps its output into one channel, each sample 2 bytes
            # long. The frame-rate/sample-rate is somewhat configurable,
            # however.
            nchannels=1, sample_width=2, framerate=framerate,
            max_chunk_count=kb_buffer_size)

    async def _read_kb_chunks_into_buffer(self, stm):
        while not stm.at_eof():
            data = None
            try:
                data = await stm.readexactly(1024)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    data = e.partial
                else:
                    continue
            await self.add_pcm_chunk(data)

    async def start(self, frequency):
        rtl_cmd = [
            shlex.quote(self._rtlfm_exec_path),
            # Configure the frequency here.
            '-f', shlex.quote('{}'.format(frequency)),
            '-s', '200k', '-r', shlex.quote('{}'.format(self.framerate)),
            '-A', 'fast', '-'
        ]
        rtl_proc = await asyncio.create_subprocess_exec(
            *rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.add_process_handle(rtl_proc)
        # Read stderr into the log.
        self.add_awaitable(asyncio.create_task(misc.read_lines_from_stream(
            rtl_proc.stderr, self.add_log_line)))
        # Read stdout into the buffer.
        self.add_awaitable(asyncio.create_task(
            self._read_kb_chunks_into_buffer(rtl_proc.stdout)
        ))
        # Add an awaitable that will set the stop event once the process
        # is stopped.
        async def stop_on_exit():
            await rtl_proc.wait()
            self._stop_requested.set()
        self.add_awaitable(asyncio.create_task(stop_on_exit()))
        # Add an awaitable that will stall until the queues are consumed
        # before fully shutting down.
        self.add_awaitable(asyncio.create_task(
            self._wait_to_join_queues()
        ))


#
# More Drivers
#
class LocalFileDriver(AbstractRtlDriver):
    """Driver that reads local files and generates PCM data from them."""

    def __init__(self):
        super(LocalFileDriver, self).__init__()

    async def start(self, frequency):
        pass
