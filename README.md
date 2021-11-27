# pysdrweb

Project to create an interface to RTL-SDR tooling.

## FM Radio Receiver

The first focus of the project is to make a simple, web-based
FM Radio receiver that can be controlled entirely through the
browser or REST API calls. These calls are all backed by some
_driver_, which implements the set of calls necessary to run
the radio.

There are currently two drivers supported.

### RTL-FM Python Native Driver

This driver works by running `rtl_fm` in a separate process,
then collecting the process's `stdout` into a sliding window
buffer. When the audio file is requested, the driver will
encode the data on the fly; this works fairly well because
some of the formats are already very similar to PCM and only
require a header on top of the raw data; other formats are
encoded and compressed from the raw PCM data itself.

This native driver supports the following formats:
 - WAV: Raw format used in CDs and other media.
 - AIFF: Another format used in places. Can potentially
        support compression.

Future work might also include m8uu (HLS playlist-style way)
for more common real-time streaming, but that is not yet
implemented.

This driver requires configuring:

 - rtl_fm: The path to the `rtl_fm` executable.
 - (optional) kb_buffer_size: Size of the buffer (in kB) to
        use for the sliding window.

### RTL-FM Icecast Driver

This driver works by running `rtl_fm`, but piping the output
to `sox`, then to `ffmpeg` to stream the data to a separate
icecast server. The driver basically does no encoding and
leaves that to the Icecast server; any request for the audio
is simply proxied verbatim to the internal Icecast server.

This driver is harder to setup, but can be useful if trying
to stream the audio to another source or another setting
while still permitting the FM frequency to be changed via
a REST API. The Icecast server may also scale better with
many clients, if it has more network bandwidth available to
it.

This driver requires configuring:
 - rtl_fm: The path to the `rtl_fm` executable.
 - sox: The path to the `sox` executable.
 - ffmpeg: The path to the `ffmpeg` executable.
 - icecast_url: The URL to the icecast server. This should
    have the format:
    `http://<source>:<password>@<host>:<port>/path`
    The client URL (used to proxy when audio is requested
    directly through this web server instead of the external
    Icecast server) is inferred from this.
