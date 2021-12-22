# pysdrweb

Project to create a web interface to RTL-SDR tooling.

## FM Radio Receiver

The first focus of the project is to make a simple, web-based
FM Radio receiver that can be controlled entirely through a
web browser. By visiting the main page, the user can listen in
on an (FM) radio station using any compatible RTL-SDR dongle
and change the frequency, as desired.

This receiver is backed by some _driver_, which implements the
set of calls necessary to run the radio, i.e. calls to generate
a raw PCM data stream that is then encoded on the fly. Usually,
this driver is simply the RTL-SDR program: `rtl_fm`, which the
server searches for by default.
(Other driver types are planned for the future, but are not yet
implemented.)

By default, python supports some (usually uncompressed) formats
out of the box:
 - WAV: Raw format used in CDs and other media.
 - AIFF: Another format used in places. Can potentially
        support compression.

If `soundfile` is also present (and installed correctly), then
this can also encode into more formats:
 - FLAC: Free Lossless Audio Codec, supported by most browsers
       with some possible compression.
 - OGG: Container format, supported by browsers. This format
       usually compresses fairly well, especially compared to
       more raw formats.
(The list isn't exhaustive, but the goal is to support more
browsers, not as many formats. The code should be easy enough
to add more formats, however, if that is ever really needed.)

Currently, this server appears to work on Firefox and Chrome,
which support dynamically playing audio as it is downloaded.
The hope is to support Safari in the future, but this is not
yet implemented.

### Goal

The goal of this server is to support multiple formats and
multiple clients simultaneously, without straining _too_ many
system resources; the hope is that this server is low-key
enough to run on weaker hardware.

## Running the Server

Currently, the server requires `rtl_fm` to be installed and
a valid RTL-SDR device to be plugged in.

The simplest way to run the server is:
```sh
sdrfm_server -p 8080 -f 107.3M
```
This runs the server on port 8080 and listens (initially) on
107.3 FM.

If `rtl_fm` is not on the path, that can be passed in with:
```sh
sdrfm_server -p 8080 -f 107.3M -m ${RTL_FM_PATH}
```

For the full set of options, it is easier to configure the
server with a configuration file. A sample configuration file
is shown below:
```yaml
#
# Sample 'native' driver configuration file.
# 
---
port: 9000
# Uncomment to require auth to change the frequency.
# auth:
#   type: basic
#   # This indicates whether 'readonly' (GET) requests require auth or not.
#   ignore_on_read: true
#   # Each user is denoted by: <user>: <password>
#   users:
#     admin: admin

# Starting (FM) channel/frequency to listen on startup.
default_frequency: 107.3M
# Driver settings.
driver:
  # Path to rtl_fm, if not on the path of the server.
  rtl_fm: /usr/local/bin/rtl_fm
  # Optional.
  kb_buffer_size: 128
```
Then, to run the server:
```sh
sdrfm_server -c config.yml
```
This permits adding authentication as well.

## Server REST API

Currently, the server supports a REST API that the main page
invokes to render. The main calls are described below:

| Method | Route | Description |
---------|-------|-------------|
| GET | `/api/frequency` | Returns the current frequency the server is listening on. |
| POST | `/api/frequency` | Change the frequency the server is listening on. |
| GET | `/api/procinfo` | Returns any (stderr) logging for the current driver. |
| GET | `/api/radio/audio.<ext>` | Fetch the audio streamed from the radio. |

### Audio Route (More Detail)

Much of this server relies on `/api/radio/audio.<ext>` to function since this
is the route that actually serves the audio file. This route uses the given
`<ext>` extension to determine the format to stream in. The route also accepts
an optional `timeout` parameter, telling the server how long to listen for (in
seconds). If not passed, the server will continue streaming indefinitely.

To download 10 seconds of the audio in WAV format (assuming the server is
running locally on port 8080) call:
```sh
curl http://localhost:8080/api/radio/audio.wav?timeout=10 > audio.wav
```

If a format is not supported, the response will indicate as such.

### Authentication

By default, the requests are not authenticated. If configured, however,
the requests can all require authentication using "HTTP Basic"-style
authentication. When making a request, the browser will prompt for the
username/password combination.

Other authentication may be added later.

## How It Works

For simplicity, the RTL-SDR tooling includes `rtl_fm`, which
streams FM audio data as raw, mono, 16-bit (2 byte) PCM data
at some configurable frequency. For example, the following
shell pipeline streams the frequency `107.3M` to an MP3 file:
```sh
# Listen on 107.3 FM, sampling at 48kHz
rtl_fm -f 107.3M -M fm -s 48000 | \
sox -traw -r48000 -es -b16 -c1 -V1 - -tmp3 - > output.mp3
```
The `sox` command is used to convert raw PCM data into framed
MP3 data. (It is possible to use `ffmpeg` in a similar vain as
well). The quality is quite bad; we need to sample more quickly
then resample down to a lower frequency:
```sh
# Sample at 200k, then resample down to 48k.
# Passing '-A fast' denotes the way to perform the resample.
rtl_fm -f 107.3M -M fm -s 200k -r 48k -A fast | \
sox -traw -r48k -es -b16 -c1 -V1 - -tmp3 - > output.mp3
```
This command can be run remotely and played (once) in the
browser using:
```sh
# Like above, but pipes the output to port 8080.
rtl_fm -f 107.3M -M fm -s 200k -r 48k -A fast | \
sox -traw -r48k -es -b16 -c1 -V1 - -tmp3 - | \
socat -u - TCP-LISTEN:8080
```
This pipeline is cool, but has some obvious problems. In
particular, the pipeline ends as soon as the browser stops
listening for any more input. Also, only one browser/client
can listen at a time. (Also, some browsers might not support
this for some formats...) Once finished, the command must be
executed again.
Changing the frequency also requires rerunning or otherwise
changing the command.

To address these issues, pysdrweb will buffer the PCM output
from whatever source (currently `rtl_fm`) and encode it on
the fly, which permits _multiple clients and formats_. This
also permits changing the frequency more easily.
