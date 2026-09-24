#!/bin/bash

DEV_MODE=""
DEV_NOTE_CONTENTS=`cat /home/admin/note_this_machine_is_dev 2>/dev/null`
if [ "$DEV_NOTE_CONTENTS" == "just in case you are doubting your sanity" ]; then
  DEV_MODE="--dev"
fi

if [ "$DEV_MODE" = "--dev" ]; then
  screen -S frontend -X quit
  sleep 1
  screen -dmS frontend python3 main.py --port 8008
  echo "DEV frontend deployment complete. New Flask dev frontend should now be running."
else
  while [[ -n $(ps aux | grep gunicor | grep unicorn) ]] ; do
    killall gunicorn
    sleep 0.1
  done
  screen -S frontend -X quit >/dev/null 2>/dev/null
  screen -dmS frontend -L -Logfile /home/admin/fe.log gunicorn -c gunicorn_config.py main:app
  echo "Prod frontend deployment complete. New gunicorn frontend should now be running."
fi
