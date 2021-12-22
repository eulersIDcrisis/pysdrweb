#!/bin/bash
#
# Run pyinstaller to create a bundle.
#
MAIN_SCRIPT="pysdrweb/fmserver/server.py"
CURR_DIR=`pwd`

pyinstaller ${MAIN_SCRIPT} -n sdrfm_server -p ${CURR_DIR} \
  --add-data "pysdrweb/fmserver/static/*:pysdrweb/fmserver/static" \
  --hidden-import "soundfile"
