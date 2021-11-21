"""config.py.

Module to parse the configuration for the server from
a basic file.

TODO: Some of this could be done in yaml, but the config
parser is likely sufficient for now.
"""
import sys
import configparser


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
        'sox_exec_path': '/usr/local/bin/sox',
        'ffmpeg_exec_path': '/usr/local/bin/ffmpeg',
    }

    config.write(stm)


def test():
    create_default_config(sys.stderr)

if __name__ == '__main__':
    test()
