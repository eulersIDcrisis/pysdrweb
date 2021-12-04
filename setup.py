"""setup.py for pysdrweb.

Installing pysdrweb.
"""
from setuptools import setup, find_packages

setup(
    name='pysdrweb',
    version='0.1.0',
    description='FM Web Radio using RTL-SDR utilities.',
    author='eulersIDcrisis',
    packages=find_packages(include=['pysdrweb', 'pysdrweb.*']),
    install_requires=[
        # Require 'tornado', minimum version of 6.0.1
        # Could possibly waive this to tornado 5.X, not sure.
        'tornado>=6.0.1',
        'PyYAML>=5.4.1',
        'click>=5.0.0',
        'SoundFile>=0.10.0',
    ],
    setup_requires=['flake8'],
    entry_points={
        'console_scripts': [
            'sdrwebserver=pysdrweb.server.main:main_cli'
        ]
    }
)
