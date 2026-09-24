import sys

from account import new_customer_insert_mongo_and_email

if len(sys.argv) == 5: # credits style
    customer_id = sys.argv[1]
    customer_name = sys.argv[2]
    cloud_region = sys.argv[3]
    admin_email = sys.argv[4]
    billing_type = "credits"
    aws_id = "unneeded"
    subnet_id = "unneeded"
    security_group_id = "unneeded"
    config_dump = "unneeded"
    new_customer_insert_mongo_and_email(customer_id, customer_name, billing_type, cloud_region,
                                        admin_email, aws_id, subnet_id, security_group_id, config_dump)
else:
    print('invalid number of arguments')
