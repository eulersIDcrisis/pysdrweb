"""server.py.

Server utilities for the FM server mode for the RTL-SDR dongle.
"""
import os
import logging
import yaml
import click
from pysdrweb.util.misc import get_version
from pysdrweb.util.auth import parse_auth_manager_from_options
from pysdrweb.util.ioloop import IOLoopContext
from pysdrweb.util.logger import get_child_logger
from pysdrweb.fmserver.driver import RtlFmExecDriver
from pysdrweb.fmserver.handlers import FmServerContext


logger = get_child_logger('fmserver')


def parse_option_dict(option_dict):
    """Parse the given dictionary and create the driver.

    Returns
    -------
    tuple of (<dict of options>, Driver)
    """
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


# Create the base command for use with this server.
@click.command()
@click.option('-c', '--config', help=(
    "Config file to read options from. Any CLI options here will override "
    "options in this config file. It is recommended to use the config file "
    "for most options, since not everything can be configured through the "
    "CLI."
), type=click.Path(readable=True, exists=True, dir_okay=False))
@click.option('-v', '--verbose', flag_value=True, help=(
    "Enable verbose of the output."))
@click.option('-p', '--port', type=click.IntRange(0, 65535), help=(
    "Port to run the server on. Calling multiple times will listen on each "
    "port. If no port is specified either here or in the config file, this "
    "will default to port 9000 for convenience."), default=[0], multiple=True)
@click.option('-f', '--frequency', type=click.STRING, help=(
    "FM frequency to use when starting the server. Overrides any default set "
    "via configuration file."))
@click.option(
    '-m', '--rtl', help="Path to the 'rtl_fm' executable.",
    type=click.Path(readable=True, exists=True))
@click.option('--unix', type=click.Path(readable=True, writable=True), help=(
    "UNIX socket to listen to the server on."))
@click.help_option('-h', '--help')
@click.version_option(get_version())
def fm_server_command(port, frequency, rtl, unix, verbose, config):
    """CLI to run the FM server via RTL-FM."""
    level = logging.DEBUG if verbose > 0 else logging.INFO
    logging.basicConfig(level=level)

    # Parse the configuration file first.
    if config:
        with open(config, 'r') as stm:
            option_dict = yaml.safe_load(stm)
    else:
        option_dict = dict()

    # Port settings.
    if port and port[0]:
        option_dict['port'] = port
    elif 'port' not in option_dict:
        # The default port is 9000
        option_dict['port'] = [9000]
    elif isinstance(option_dict['port'], int):
        # In case the result is a tuple instead of a list.
        option_dict['port'] = [option_dict['port']]
    # UNIX domain socket.
    if unix:
        curr_ports = option_dict.get('port', [])
        if isinstance(curr_ports, int):
            curr_ports = [curr_ports]
        elif not isinstance(curr_ports, list):
            curr_ports = list(curr_ports)
        curr_ports.append('unix:{}'.format(unix))
        option_dict['port'] = curr_ports
    # Final, parsed ports
    ports = option_dict['port']

    # Custom rtl_fm exec path.
    if rtl:
        driver_dict = option_dict.get('driver', {})
        if driver_dict:
            option_dict['driver']['rtl_fm'] = rtl
    if frequency:
        option_dict['default_frequency'] = frequency

    # Create the driver.
    auth_manager = parse_auth_manager_from_options(option_dict)
    driver = RtlFmExecDriver.from_config(option_dict.get('driver', {}))
    context = FmServerContext(
        driver, auth_manager, option_dict.get('default_frequency'))

    app = context.generate_app()
    server = IOLoopContext()
    server.ioloop.add_callback(context.start)
    server.create_http_server(app, ports)
    port_msg = ', '.join(['{}'.format(port) for port in ports])
    logger.info("Running server on ports: %s", port_msg)
    server.run()


if __name__ == '__main__':
    fm_server_command()
