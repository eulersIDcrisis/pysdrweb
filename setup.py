"""setup.py for pysdrweb.

Installing pysdrweb.
"""
from setuptools import setup, find_packages
import os

def get_version():
    version = {}
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), 'pysdrweb', '__about__.py'
    ))
    with open(path, 'r') as stm:
        code = stm.read()
    exec(code, version)
    return version.get('__version__', '(unknown)')


VERSION = get_version()


# NOTE: The 'version' is set via setup.cfg.
setup(
    name='pysdrweb',
    version=VERSION,
    description='FM Web Radio using RTL-SDR utilities.',
    author='eulersIDcrisis',
    packages=find_packages(include=['pysdrweb', 'pysdrweb.*']),
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
    setup_requires=['flake8'],
    entry_points={
        'console_scripts': [
            'sdrfm_server=pysdrweb.server.main:main_cli'
        ]
    }
)
