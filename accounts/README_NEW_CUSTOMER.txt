NEW SUBSCRIPTION CUSTOMER FLOW NOTES:
  * here in the accounts directory (in a clean git repo), run ./new_customer.sh
    - (it will ask for their company name, admin email, billing type, cloud region)
  * point them to the URL that new_customer.sh prints, and instruct them to run the downloaded script
  * the script will automatically submit the configuration to our system and trigger the admin email
  * tell the customer to check their admin email to create an account (and to authorize their employees' emails to make accounts too)


NEW BILLING CUSTOMER:
  * run ./new_customer.sh here in the accounts directory (in a clean git repo).
    - (it will ask for their company name, admin email, billing type, cloud region)
  * It will give you an invocation of new_customer_mongo_insert_and_email.py to run (ON PROD FRONTEND!)
  * Running that (ON PROD FRONTEND IN frontend/) will send their admin email an account creation email
  * tell the customer to check their admin email to create an account (and to authorize their employees' emails to make accounts too)
