"""main.py.

Main code for running the basic server.
"""
import signal
import logging
import argparse
from urllib.parse import urlsplit, urlunsplit
from tornado import ioloop
from pysdrweb.driver.common import find_executable
from pysdrweb.driver.icecast import IcecastRtlFMDriver
from pysdrweb.driver.rtl_native import RtlFMNativeDriver
from pysdrweb.server.handlers import Server


def run():
    parser = argparse.ArgumentParser(
        description="Run an web server to play FM Radio using RTL-SDR.")
    parser.add_argument('-v', '--verbose', action='count',
                        help="Increase verbosity.", default=0)
    parser.add_argument('-p', '--port', type=int, help="Port to listen on.")
    parser.add_argument('--rtl-fm', type=str, default=None,
                        help="Path to the 'rtl_fm' executable.")
    parser.add_argument('--sox', type=str, default=None,
                        help="Path to the 'sox' executable.")
    parser.add_argument('--ffmpeg', type=str, default=None,
                        help="Path to the 'ffmpeg' executable.")
    parser.add_argument(
        '--icecast-url', type=str, help=(
        "Icecast URL, including user/password field."),
        default="icecast://source:hackme@localhost:8000/radio")

    args = parser.parse_args()

    if args.verbose > 0:
        level = logging.DEBUG
    else:
        level = logging.INFO

    # Setup logging.
    logging.basicConfig(level=level)

    # Set the port.
    port = args.port

    # Parse the executable paths.
    if args.rtl_fm:
        rtl_path = args.rtl_fm
    else:
        rtl_path = find_executable('rtl_fm')

    if args.sox:
        sox_path = args.sox
    else:
        sox_path = find_executable('sox')

    if args.ffmpeg:
        ffmpeg_path = args.ffmpeg
    else:
        ffmpeg_path = find_executable('ffmpeg')

    # Parse the icecast URL
    _, netloc, path, query, fragment = urlsplit(args.icecast_url)
    parts = netloc.split('@')
    if len(parts) > 1:
        netloc = parts[1]
    client_url = urlunsplit(('http', netloc, path, query, fragment))
    config = dict(
        rtl_fm=rtl_path, sox=sox_path, ffmpeg=ffmpeg_path,
        icecast_url=args.icecast_url, client_url=client_url
    )
    # driver = IcecastRtlFMDriver(config)
    driver = RtlFMNativeDriver(config)

    server = Server(driver, port=port)
    def _sighandler(signum, stack_frame):
        server.stop(from_signal_handler=True)

    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    server.run()


if __name__ == '__main__':
    run()
