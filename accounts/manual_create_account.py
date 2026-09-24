from pymongo import MongoClient
import bcrypt

mongo_client = MongoClient('mongodb://127.0.0.1:27017/')
mongo_willow_db = mongo_client['willow_database']
mongo_accounts_col = mongo_willow_db['users']

email = input('email: ')
pw = input('pw: ')
cust_id = input('customer ID (int): ')
region = input('preferred region (us-east-1 or us-west-1): ')
create_as_admin = (input('admin account? (yes/no) ') == 'yes')

pw_bytes = pw.encode('utf-8')
pw_salt_and_hash = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())

mongo_accounts_col.insert_one(
    {'useremail': email,
     'pw_salt_and_hash': bcrypt.hashpw(pw_bytes, bcrypt.gensalt()),
     'deactivated': False,
     'customer_id': cust_id,
     'preferred_region': region,
     'library_filter': '', # blank means show everything
     'is_admin': create_as_admin
    })
