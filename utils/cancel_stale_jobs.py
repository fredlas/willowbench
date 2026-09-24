import pymongo
from pymongo import MongoClient
import datetime as dt
import os
import requests
import json

try:
  client = MongoClient('mongodb://127.0.0.1:27017/')
  db = client['willow_database']
  collection = db['jobs']

  # Find documents where last_activity_sse is more than 1 day old
  cutoff_time = dt.datetime.now() - dt.timedelta(1)
  cutoff_timestamp = int(cutoff_time.timestamp())
  print(f"cutoff time is {cutoff_time}")
  query = {"last_activity_sse": {"$lt": cutoff_timestamp}}
  old_activity_documents = collection.find(query)
  for doc in old_activity_documents:
    try:
      job_id = doc['job_id']
      status = doc['status']
      if status.startswith('running') or status == 'pending':
        print(f"Cancelling stale job_id {job_id}")
        # Send job_id in JSON body
        response = requests.post("https://bench.willowbench.bio/core/cancel_workflow", json={'job_id': job_id})
        response.raise_for_status()
    except Exception as e:
      print(f"Error cancelling job {job_id}: {e}")

except pymongo.errors.ConnectionFailure as e:
  print(f"Could not connect to MongoDB: {e}")
except Exception as e:
  print(f"An unexpected error occurred: {e}")
finally:
  if 'client' in locals() and client:
    client.close()
