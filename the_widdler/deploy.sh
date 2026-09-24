#!/bin/bash

NUM_WIDDLERS=$1


#e.g.:
#20250609-223344 6666
#20250611-040103 6667
#99999999-999999 6668
CURRENT_PORT=`tail -n1 /home/admin/willow/core/herder_cutoff_map.txt | sed 's/.* //'`
if [ -z "$CURRENT_PORT" ]; then
  CURRENT_PORT="6660"
fi
PORT_RANGE_START=6660
PORT_RANGE_END=6669
PORT_COUNT=$((PORT_RANGE_END - PORT_RANGE_START + 1))
NEW_PORT=$(( (CURRENT_PORT - PORT_RANGE_START + 1) % PORT_COUNT + PORT_RANGE_START ))
echo "Deploying Widdler Herder, old port $CURRENT_PORT new port $NEW_PORT, $NUM_WIDDLERS widdlers"

# Smooth backend redeployment:
#1) start new herder on e.g. 6666
#2) wait for /health ok (waits for all widdlers to be ready)
#3) rewrite the current 99999999-999999 cutoff to be now+5s, then append "99999999-999999 6666"
#4) kick off a core redeploy
#5) wait until the new +5 seconds cutoff has passed
#6) hit /oh_boy_lets_shutdown_shutdown on 6665 (i.e. the +5s cutoff one) to initiate graceful shutdown
#   (remember, might take hours to resolve)
#7) TODO remove the shut down one from the cutoffs config TODO TODO

#1
echo "now starting new widdler herder (on port $NEW_PORT)..."
screen -S "herder$NEW_PORT" -L -Logfile /home/admin/widdler.log -dm sh -c "cd /home/admin/willow/the_widdler && ./target/release/herder $NUM_WIDDLERS $NEW_PORT /home/admin/willow/workflows"

#2
TRIES=0
while [ "$(curl --silent "http://[::1]:$NEW_PORT/health")" != "ok" ]; do
  sleep 0.1
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -eq 300 ]; then
    echo "!!!FATAL!!! herder didn't come online within 30 seconds. Here is tail -n50 widdler.log:"
    tail -n50 /home/admin/widdler.log
    exit 1
  fi
done

#3
FUT_5S_RAW=$(date -d "+5 seconds" +%s)
FUT_5S_FORMATTED=$(date -d "@$FUT_5S_RAW" +%Y%m%d-%H%M%S)
echo "now updating herder cutoffs config... previous herder will be given cutoff $FUT_5S_FORMATTED"
sed -i "s/99999999-999999/$FUT_5S_FORMATTED/" /home/admin/willow/core/herder_cutoff_map.txt
echo "99999999-999999 $NEW_PORT" >>/home/admin/willow/core/herder_cutoff_map.txt

echo "========================================================================="
echo "the_widdler deploy subroutine: now redeploying core to pick up new cutoffs..."
#4
pushd /home/admin/willow/core >/dev/null
  ./deploy.sh
popd >/dev/null
echo "========================================================================="

#5
while true; do
  CURRENT_TIME_RAW=$(date +%s)
  if (( CURRENT_TIME_RAW >= FUT_5S_RAW )); then
    break
  fi
  sleep 0.1
done

#6
echo "now beyond new cutoff, so now initiating shutdown of old herder."
echo "    (remember, herder shutdown won't complete until all workflows done, maybe hours from now)"
curl --silent -X POST http://[::1]:$CURRENT_PORT/oh_boy_lets_shutdown_shutdown

echo "Herder deployment to new port $NEW_PORT complete."
