"""server.py.

Server utilities for the FM server mode for the RTL-SDR dongle.
"""

# Standard Imports
import logging
import argparse

# Third-party Imports
import yaml

# Local Imports
from pysdrweb.util.misc import get_version
from pysdrweb.util.event_loop_util import EventLoopContext
from pysdrweb.util.logger import get_child_logger
from pysdrweb.fmserver.handlers import generate_app
from pysdrweb.fmserver.context import parse_option_dict


logger = get_child_logger("fmserver")


class FmServerContext(EventLoopContext):

    def __init__(self, context, ports):
        super().__init__()
        self._context = context
        self._ports = ports

    async def initialize(self):
        await super().initialize()

        # Run the context.
        await self._context.start()
        self.add_drain_hook(self._context.stop)

        app = generate_app(self._context)
        self.create_http_server(app, self._ports)


# Create the base command for use with this server.
def parse_cli_options() -> tuple[FmServerContext, list[int | str]]:
    parser = argparse.ArgumentParser(
        description="""
Run the Pysdrweb server that listens on the radio using an RTL-SDR dongle.

In order to support more browsers, the server supports converting the radio
signal to various audio formats. Various options can tune different aspects
of the server as appropriate.
"""
    )
    parser.add_argument(
        "config",
        type=str,
        nargs="?",
        default=None,
        help=(
            "Config file to read options from. Any CLI options here will override "
            "options in this config file. It is recommended to use the config file "
            "for most options, since not everything can be configured through the "
            "CLI."
        ),
    )
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help=("Dump starter configuration file and exit."),
    )
    parser.add_argument(
        "-p",
        "--port",
        action="append",
        type=int,
        default=None,
        help=(
            "Port to run the server on. Calling multiple times will listen on each "
            "port. If no port is specified either here or in the config file, this "
            "will default to port 9000 for convenience."
        ),
    )
    parser.add_argument(
        "--unix",
        type=str,
        action="append",
        help=("UNIX socket to listen to the server on."),
    )
    parser.add_argument("-m", "--rtl", help="Path to the rtl_fm executable.")
    parser.add_argument(
        "-f",
        "--frequency",
        type=str,
        help=(
            "FM frequency to use when starting the server. Overrides any default set "
            "via configuration file."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Enable more verbose output."
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s: {get_version()}",
        help="Print the version and exit.",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose > 0 else logging.INFO
    logging.basicConfig(level=level)

    # Parse the configuration file first.
    config_path = "config.yaml"
    if args.config:
        config_path = args.config
        with open(config_path, "r", encoding="utf-8") as stm:
            option_dict = yaml.safe_load(stm)
    else:
        option_dict = {}

    ports = []
    if args.port:
        for port in args.port:
            if port >= 65536 or port <= 0:
                raise argparse.ArgumentError(
                    "port", "--port requires a valid port number!"
                )
            ports.append(port)
    # Port settings.
    if ports and ports[0]:
        option_dict["port"] = ports
    elif "port" not in option_dict:
        # The default port is 9000
        option_dict["port"] = [9000]
    elif isinstance(option_dict["port"], int):
        # In case the result is a tuple instead of a list.
        option_dict["port"] = [option_dict["port"]]
    # UNIX domain socket.
    for unix in args.unix or []:
        curr_ports = option_dict.get("port", [])
        if isinstance(curr_ports, int):
            curr_ports = [curr_ports]
        elif not isinstance(curr_ports, list):
            curr_ports = list(curr_ports)
        curr_ports.append(f"unix:{unix}")
        option_dict["port"] = curr_ports
    # Final, parsed ports
    ports = option_dict["port"]

    # Custom rtl_fm exec path.
    if args.rtl:
        driver_dict = option_dict.get("driver", {})
        if driver_dict:
            option_dict["driver"]["rtl_fm"] = args.rtl
    if args.frequency:
        option_dict["default_frequency"] = args.frequency

    # Dump the configuration file if requested.
    if args.dump_config:
        # Write the configuration file.
        with open(config_path, "w") as stm:
            yaml.dump(option_dict, stm)
        parser.exit(0, f"Wrote out the config file to: {config_path}")

    # Create the context.
    return parse_option_dict(option_dict), ports


def fm_server_command():
    """CLI to run the FM server via RTL-FM."""
    context, ports = parse_cli_options()
    server = FmServerContext(context, ports)

    port_msg = ", ".join([f"{p}" for p in ports])
    logger.info("Running server on ports: %s", port_msg)
    server.run()


if __name__ == "__main__":
    fm_server_command()
