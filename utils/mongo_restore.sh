#!/bin/bash

# ideally you should take down frontend and core before running this, but it's probably fine

BACKUP_FILE=$1

if [ -z "$BACKUP_FILE" ]; then
  echo "this script needs a mongo .tar.gz backup passed to it as its first argument"
  exit 1
fi

# Extract the backup
tar -xzvf "$BACKUP_FILE"

# Restore the database
mongorestore --nsInclude="willow_database.jobs" temp_mongo_bkup
mongorestore --nsInclude="willow_database.users" temp_mongo_bkup
mongorestore --nsInclude="willow_database.in_flight_email_verifications" temp_mongo_bkup

# Remove the temporary backup directory
rm -rf temp_mongo_bkup

echo "Mongo database restored from $BACKUP_FILE."
