"""setup.py for pysdrweb.

Installing pysdrweb.
"""
from setuptools import setup


VERSION = '0.3.0'


# NOTE: The 'version' is set via setup.cfg.
setup(
    version=VERSION,
    keywords='radio fm rtl-sdr rtl_fm',
    install_requires=[
        # Require 'tornado', minimum version of 6.0.1
        # Could possibly waive this to tornado 5.X, not sure.
        'tornado>=6.0.1',
        'PyYAML>=5.4.1',
        'click>=5.0.0',
        # SoundFile might require libsnd to be installed manually.
        # We _could_ make this dependency optional, possibly, but
        # 'native' python sound formats are uncompressed and quite
        # large.
        'SoundFile>=0.10.0',
    ],
    extras_require={
        # Lameenc is a python module for LAME, the MP3 encoder.
        'mp3': ['lameenc>=1.3.0']
    },
    setup_requires=['flake8'],
    entry_points={
        'console_scripts': [
            'sdrfm_server=pysdrweb.fmserver.server:fm_server_command'
        ]
    }
)
