#!/bin/bash


CURRENT_PORT=`cat /home/admin/willow/frontend/willow_backend_port.txt`
if [ -z "$CURRENT_PORT" ]; then
  CURRENT_PORT="7770"
fi
PORT_RANGE_START=7770
PORT_RANGE_END=7779
PORT_COUNT=$((PORT_RANGE_END - PORT_RANGE_START + 1))
NEW_PORT=$(( (CURRENT_PORT - PORT_RANGE_START + 1) % PORT_COUNT + PORT_RANGE_START ))


DEV_MODE=""
DEV_NOTE_CONTENTS=`cat /home/admin/note_this_machine_is_dev 2>/dev/null`
if [ "$DEV_NOTE_CONTENTS" == "just in case you are doubting your sanity" ]; then
  DEV_MODE="--dev"
fi

echo "Deploying core, old port $CURRENT_PORT new port $NEW_PORT   $DEV_MODE"

# Smooth backend redeployment:
#1) Start new backend on say port 7771 (given that previous was 7770)
#2) Wait for /health to be ok
#3) Overwrite 7771 onto frontend/willow_backend_port.txt
#4) curl -X POST http://[::1]:7770/begin_shutdownzzz_shutdown_yes_please

#1
echo "now starting new $DEV_MODE core on port $NEW_PORT..."
if [ "$DEV_MODE" = "--dev" ]; then
  screen -S "core$NEW_PORT" -dm sh -c "cd /home/admin/willow/core && ./target/release/core /home/admin/willow --port $NEW_PORT --dev"
else
  screen -S "core$NEW_PORT" -L -Logfile /home/admin/core.log -dm sh -c "cd /home/admin/willow/core && ./target/release/core /home/admin/willow --port $NEW_PORT"
fi

#2
TRIES=0
while [ "$(curl --silent "http://[::1]:$NEW_PORT/health")" != "ok" ]; do
  sleep 0.1
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -eq 100 ]; then
    echo "!!!FATAL!!! core didn't come online within 10 seconds"
    exit 1
  fi
done

#3
echo "now writing $NEW_PORT to frontend config."
echo "$NEW_PORT" > /tmp/willow_backend_port.txt
mv /tmp/willow_backend_port.txt /home/admin/willow/frontend/willow_backend_port.txt

#3.5 HACK we don't have a clean shutdown mechanism, so give a few seconds for final requests to resolve
sleep 3

#4
echo "now doing IMMEDIATE shutdown of old core."
curl --silent -X POST http://[::1]:$CURRENT_PORT/begin_shutdownzzz_shutdown_yes_please

echo "Core deployment complete. New $DEV_MODE backend running on port $NEW_PORT."
