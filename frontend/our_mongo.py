from pymongo import MongoClient
import bcrypt

import utils
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

mongo_client = MongoClient('mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&appName=willowfe')
mongo_willow_db = mongo_client['willow_database']



mongo_accounts_col = mongo_willow_db['users']

def find_user(useremail):
  if not utils.is_valid_email(useremail):
    return None
  found = mongo_accounts_col.find_one({'useremail': useremail})
  return found

def db_commit_username_and_pw(email, pw_bytes, customer_id, region, create_as_admin=False):
  pw_salt_and_hash = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
  if find_user(email):
    mongo_accounts_col.update_one(
        {'useremail': email },
        {'$set':
            {'pw_salt_and_hash': pw_salt_and_hash }})
  else:
    mongo_accounts_col.insert_one(
        {'useremail': email,
         'pw_salt_and_hash': pw_salt_and_hash,
         'deactivated': False,
         'customer_id': customer_id,
         'preferred_region': region,
         'library_filter': '', # blank means show everything
         'is_admin': create_as_admin
         })

def user_and_pw_match_db(email, pw_bytes):
  if not utils.is_valid_email(email):
    return False
  found = mongo_accounts_col.find_one({'useremail': email})
  if not found or found.get('deactivated'):
    return False
  return bcrypt.checkpw(pw_bytes, found['pw_salt_and_hash'])

def get_customer_id(useremail):
    user = mongo_accounts_col.find_one({'useremail': useremail})
    if user and 'customer_id' in user:
        return user['customer_id']
    return None

def set_user_library_filter(useremail, filter_str):
  mongo_accounts_col.update_one(
      {'useremail': useremail},
      {'$set': {'library_filter': filter_str}}
  )


def update_job_comment(useremail, job_id, comment):
    result = mongo_jobs_col.update_one(
        {'job_id': job_id, 'owning_useremail': useremail},
        {'$set': {'comment': comment}}
    )
    return result.modified_count > 0 # Return True if a document was modified

def update_job_nickname(useremail, job_id, nickname):
    result = mongo_jobs_col.update_one(
        {'job_id': job_id, 'owning_useremail': useremail},
        {'$set': {'nickname': nickname}}
    )
    return result.modified_count > 0 # Return True if a document was modified

mongo_inflight_col = mongo_willow_db['in_flight_email_verifications']

def insert_inflight_code(email_ver_str, email_addr, expiration, customer_id, create_as_admin):
  mongo_inflight_col.insert_one({
    'code': email_ver_str,
    'email': email_addr,
    'expiration': expiration,
    'customer_id': customer_id,
    'create_as_admin': create_as_admin
  })


def find_inflight_code(code):
  return mongo_inflight_col.find_one({'code': code})

def delete_inflight_code(code):
  mongo_inflight_col.delete_one({'code': code})




mongo_customers_col = mongo_willow_db['customers']
def insert_new_customer(customer_id, name, billing_type, cloud_region, admin_email,
                        aws_id, subnet_id, security_group_id, config_dump):
  if mongo_customers_col.find_one({'customer_id': customer_id}):
    log_warn(f"Customer ID {customer_id} already exists. Skipping customer insert.")
    return False

  mongo_customers_col.insert_one({
    'customer_id': customer_id,
    'customer_name': name,
    'last_sub_payment_sse': 0, # last subscription payment, seconds since Unix epoch
    'credits_balance': 0,
    'billing_type': billing_type,
    'cloud_region': cloud_region,
    'admin_email': admin_email,
    'aws_id': aws_id,
    'subnet_id': subnet_id,
    'security_group_id': security_group_id,
    'stripe_customer_id': None,
    'api_token_hash': None, # TODO TODO TODO TODO generate and convey to them
    'config_dump': config_dump
  })
  log_info(f"Inserted new customer record for ID: {customer_id}")
  return True

def get_customer_region(customer_id):
  customer = mongo_customers_col.find_one({'customer_id': customer_id})
  if customer:
    return customer.get('cloud_region')
  return None

def get_customer_details(customer_id):
  return mongo_customers_col.find_one({'customer_id': customer_id})

def find_customer_by_api_token(token_bytes):
  customers_with_tokens = mongo_customers_col.find({'api_token_hash': {'$exists': True, '$ne': None}})
  for customer in customers_with_tokens:
    if bcrypt.checkpw(token_bytes, customer['api_token_hash']):
      return customer
  return None

def update_customer_stripe_id(willow_customer_id, stripe_customer_id):
    mongo_customers_col.update_one(
        {'customer_id': willow_customer_id},
        {'$set': {'stripe_customer_id': stripe_customer_id}}
    )

def add_credits(willow_customer_id, amount):
    mongo_customers_col.update_one(
        {'customer_id': willow_customer_id},
        {'$inc': {'credits_balance': amount}}
    )
    log_info(f"Added {amount} credits to Willow customer_id {willow_customer_id}. New balance: {get_customer_details(willow_customer_id).get('credits_balance')}")


mongo_jobs_col = mongo_willow_db['jobs']

def all_jobs_for_user(useremail):
  return mongo_jobs_col.find({'owning_useremail': useremail})

def get_job(useremail, job_id, allow_same_customer=True):
  # First, try to find jobs owned by the user
  job = mongo_jobs_col.find_one({'job_id': job_id, 'owning_useremail': useremail})

  # If not found, broaden to all users belonging to the same customer
  if allow_same_customer and not job:
      customer_id = get_customer_id(useremail)
      if customer_id:
          job = mongo_jobs_col.find_one({'job_id': job_id, 'customer_id': customer_id})

  return job

def find_jobs_for_user_with_filters(useremail=None, customer_id=None, time_cutoff_sse=0, workflow_substring='', status='all'):
    query = {}
    if customer_id:
        query['customer_id'] = customer_id
    elif useremail:
        query['owning_useremail'] = useremail
    else:
        # Should not happen if called correctly, but as a safeguard:
        return mongo_jobs_col.find({'_id': None}) # Return empty cursor

    if time_cutoff_sse > 0:
        query['last_activity_sse'] = {'$gte': time_cutoff_sse}

    if workflow_substring:
        # Case-insensitive substring match using regex
        query['workflow_name'] = {'$regex': workflow_substring, '$options': 'i'}

    if status != 'all':
        query['status'] = status

    return mongo_jobs_col.find(query)
