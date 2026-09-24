from pymongo import MongoClient
import datetime as dt

mongo_client = MongoClient('mongodb://127.0.0.1:27017/')
mongo_willow_db = mongo_client['willow_database']
mongo_accounts_col = mongo_willow_db['users']
mongo_inflight_col = mongo_willow_db['in_flight_email_verifications']

# Remove expired registrations
mongo_inflight_col.delete_many({'expiration': {'$lt': dt.datetime.now()}})
