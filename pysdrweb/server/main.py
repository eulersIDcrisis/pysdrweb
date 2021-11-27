"""main.py.

Main code for running the basic server.
"""
import sys
import signal
import logging
# Third-party imports.
import yaml
import click
# Local imports.
from pysdrweb.util.logger import logger
from pysdrweb.server.handlers import Server
from pysdrweb.driver.loader import get_driver_mapping


def parse_config_file(path):
    """Parse the configuration file and create the driver.

    Returns
    -------
    tuple of (<dict of options>, Driver)
    """
    with open(path, 'r') as stm:
        option_dict = yaml.safe_load(stm)

    # Check for the 'driver' option. This MUST be set to one of
    # the available drivers in the system.
    driver_name = option_dict.get('driver')
    if not driver_name:
        raise Exception("No driver configured!")

    # Fetch the available drivers and find a match.
    driver_mapping = get_driver_mapping()
    driver_type = driver_mapping.get(driver_name)
    if not driver_type:
        raise Exception(
            "Driver (type: {}) is not supported!".format(driver_name)
        )
    # Be nice and assume an empty dictionary if there are no options
    # configured for the current driver.
    driver_options = option_dict.get(driver_name, dict())
    driver = driver_type(driver_options)
    return option_dict, driver


PYSDRWEB_VERSION = '0.1.0'


def create_root_cli():
    @click.group(context_settings=dict(
        help_option_names=['-h', '--help']
    ))
    @click.version_option(PYSDRWEB_VERSION)
    def _cli():
        pass

    return _cli


main_cli = create_root_cli()


@main_cli.command('driver-info', help=(
    "Inspect information about existing drivers."))
def list_drivers():
    """List the available drivers."""
    mapping = get_driver_mapping()
    print("Available drivers:")
    for name in mapping.keys():
        print(name)


@main_cli.command('run', help=(
    "Run the SDR Web server, using the config file and options. "))
@click.argument('config_file', type=click.Path(
    exists=True, file_okay=True, readable=True))
@click.option('-p', '--port', type=click.IntRange(1, 65535), help=(
    "Port to run the server. Overrides any option set in the config file."))
@click.option('-v', '--verbose', count=True, default=0, help=(
    "Enable verbose output. This option stacks for increasing verbosity."))
def main_run(config_file, port, verbose):
    """Run the server."""
    # Setup the logging first.
    level = logging.DEBUG if verbose > 0 else logging.INFO
    logging.basicConfig(level=level)

    try:
        option_dict, driver = parse_config_file(config_file)
    except Exception as exc:
        logger.critical("Error parsing config: %s", exc)
        if verbose:
            logger.exception("Including traceback...")
        sys.exit(1)

    # Parse the port.
    server_port = option_dict.get('port')
    if port:
        server_port = port
    # Assert the port is valid.
    if not port:
        raise click.BadParameter("Invalid port parameter!")

    # Setup logging.
    logging.basicConfig(level=level)

    # Now, start the server as would be expected.
    server = Server(driver, port=server_port)

    # Setup the signal handler.
    def _sighandler(signum, stack_frame):
        server.stop(from_signal_handler=True)
    signal.signal(signal.SIGINT, _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    # Run the server. This will exit when done.
    server.run()


if __name__ == '__main__':
    main_cli()
