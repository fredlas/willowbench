import os

import utils

from dotenv import load_dotenv
load_dotenv("/home/admin/willow_fe_env")
new_customer_salt = os.environ.get('WILLOW_FRONTEND_NEW_CUSTOMER_SALT')
if not new_customer_salt:
    utils.crash("WILLOW_FRONTEND_NEW_CUSTOMER_SALT environment variable not set!")

# TODO willow_backend_port.txt is now mv'd into, so maybe do that listen-for-filesystem-events thing?
def get_backend_port():
    try:
        with open("willow_backend_port.txt", "r") as f:
            return f.read().strip()
    except Exception as e:
        utils.crash(f"Failed to read backend port: {e}")
    return 0
