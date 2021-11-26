"""config.py.

Module to parse the configuration for the server from
a basic file.

TODO: Some of this could be done in yaml, but the config
parser is likely sufficient for now.
"""
import sys
import configparser


RTLSDR_SERVER_VERSION = '0.1.0'


def load_config(path):
    parser = configparser.ConfigParser()
    with open(path, 'r') as stm:
        config = parser.read_file(stm, source=path)
    return config


def create_default_config(stm):
    config = configparser.ConfigParser()

    config['DEFAULT'] = {
        'port': '9000',
    }
    config['RTL_FM'] = {
        'rtl_fm_path': '/usr/local/bin/rtl_fm'
    }

    config['ICECAST_DRIVER'] = {
        'icecast_url': 'http://source:hackme@localhost:8000/radio',
        'icecast_format': 'mp3',
        'sox_exec_path': '/usr/local/bin/sox',
        'ffmpeg_exec_path': '/usr/local/bin/ffmpeg',
    }

    config.write(stm)


def test():
    create_default_config(sys.stderr)

if __name__ == '__main__':
    test()
