#!/bin/bash

read -p "Customer company name: " CUSTOMER_NAME

read -p "Customer admin email: " CUSTOMER_ADMIN_EMAIL

read -p "Customer billing type (subscription or credits): " CUSTOMER_BILLING_TYPE
if [[ "$CUSTOMER_BILLING_TYPE" != "subscription" && "$CUSTOMER_BILLING_TYPE" != "credits" ]]; then
  echo "must be either subscription or credits"
  exit 1
fi

if [[ "$CUSTOMER_BILLING_TYPE" == "credits" ]]; then
  echo "credits not yet supported; grep for and resolve 'credits blocker' throughout the codebase"
  exit 1
fi

read -p "Customer cloud region: " CUSTOMER_CLOUD_REGION
if [[ "$CUSTOMER_CLOUD_REGION" != "us-east-1" && "$CUSTOMER_CLOUD_REGION" != "us-west-1" ]]; then
  echo "must be either us-east-1 or us-west-1"
  exit 1
fi


COUNT_USED=`wc -l customers.tsv | sed 's/ .*//'`
COUNT_USED=$((COUNT_USED + 1))
NEXT_ID=`zcat customer_ids.gz | head -n "$COUNT_USED" | tail -n 1`
SHOULD_BE_EMPTY=`grep $NEXT_ID customers.tsv`
if [ -n "$SHOULD_BE_EMPTY" ]; then
  echo "ERROR: next customer_id was supposed to be $NEXT_ID but it is already present in customers.tsv..."
  exit 1
fi
echo -e "$CUSTOMER_NAME\t$NEXT_ID\t$CUSTOMER_ADMIN_EMAIL\t$CUSTOMER_BILLING_TYPE\t$CUSTOMER_CLOUD_REGION" >>customers.tsv

echo "assigning ID $NEXT_ID to customer $CUSTOMER_NAME and committing to git"
git add customers.tsv >/dev/null 2>/dev/null
git commit -m "added $CUSTOMER_NAME to customers.tsv with ID $NEXT_ID" >/dev/null 2>/dev/null
git push >/dev/null 2>/dev/null


if [[ "$CUSTOMER_BILLING_TYPE" == "subscription" ]]; then
  THE_ID=`echo "$CUSTOMER_NAME
$CUSTOMER_ADMIN_EMAIL
$NEXT_ID" | base64`

  read -p "WILLOW_FRONTEND_NEW_CUSTOMER_SALT (find it in passwords.tc or /home/admin/willow_fe_env on prod machine): " WILLOW_FRONTEND_NEW_CUSTOMER_SALT

  cd ../frontend
  VERIFICATION=`python3 -c "from utils import generate_customer_hash ; print(generate_customer_hash($NEXT_ID, '$WILLOW_FRONTEND_NEW_CUSTOMER_SALT'))"`
  cd ../accounts

  echo ""
  echo "==========================================================================="
  echo ""
  echo ""
  echo ""
  echo "direct the customer to download and run https://bench.willowbench.bio/willowbench_format_new_customer_aws_vpc.py?customer_id_blob=$THE_ID&cloud_region=$CUSTOMER_CLOUD_REGION&verification=$VERIFICATION"
  echo ""
  echo "The script will automatically submit the configuration to our system."
else
  echo ""
  echo "==========================================================================="
  echo ""
  echo ""
  echo ""
  echo "Go run the following command on prod frontend machine (in willow/frontend):"
  echo "python3 new_customer_mongo_insert_and_email.py $NEXT_ID \"$CUSTOMER_NAME\" $CUSTOMER_CLOUD_REGION \"$CUSTOMER_ADMIN_EMAIL\""
fi
