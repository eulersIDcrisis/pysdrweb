#!/bin/bash
#
# Run pyinstaller to create a bundle.
#
MAIN_SCRIPT="pysdrweb/server/main.py"
CURR_DIR=`pwd`

pyinstaller ${MAIN_SCRIPT} -n sdr_server -p ${CURR_DIR} \
  --add-data "pysdrweb/static/*:pysdrweb/static" \
  --hidden-import "soundfile"

