#!/usr/bin/env python3

import argparse
import json
import sys
import boto3
from botocore.config import Config as Boto3Config

ROLE_NAME_IN_CUSTOMER_ACCOUNT = 'WillowbenchTaskLauncherRole'

# --- UserData Bash Script Template ---
# This script installs dependencies, downloads the template, formats it, and runs it.
user_data_template = """#!/bin/bash

# TODO conditional
apt-get update -y && apt-get install -y python3-pip python3-boto3 python3-requests curl xfsprogs mdadm

# Setup scratch space: detect instance store volumes and prepare them for use (RAID0 if multiple).
sudo mkdir -p /willow_scratch
INSTANCE_STORE_DISKS=$(lsblk | grep '^nvme' | sed 's/ .*//' | sed s/nvme0n1//)
if [ -z "$INSTANCE_STORE_DISKS" ]; then
  echo "No instance store detected; just using root volume for scratch space."
  # install_missing
else
  THE_DEVS=$(echo $INSTANCE_STORE_DISKS | sed 's,nvme,/dev/nvme,g')
  NUM_DISKS=$(echo "$THE_DEVS" | wc -w)
  if [ "$NUM_DISKS" -eq 1 ]; then
    echo "One instance store volume detected at $THE_DEVS. Formatting and mounting."
    # check_package xfsprogs
    # install_missing
    mkfs.xfs "$THE_DEVS"
    mount "$THE_DEVS" /willow_scratch
  else
    echo "$NUM_DISKS instance store volumes detected. Creating RAID0 array from: $THE_DEVS"
    # check_package mdadm
    # check_package xfsprogs
    # install_missing
    sudo mdadm --create --verbose /dev/md0 --level=0 --raid-devices="$NUM_DISKS" $THE_DEVS
    sudo mkfs.xfs /dev/md0
    sudo mount /dev/md0 /willow_scratch
  fi
fi
chmod 1777 /willow_scratch

sudo mkdir -p /willow_system
chmod 1777 /willow_system

echo "Downloading outpost template..."
curl -o /willow_system/outpost_template.py "{frontend_base_url}/outpost_template.py"

# Write formatter script
cat << 'EOF' > /willow_system/format_outpost.py
import sys

with open("/willow_system/outpost_template.py") as f:
    template = f.read()

formatted = template.format(
    frontend_base_url_val=sys.argv[1],
    task_secret_val=sys.argv[2],
    aws_region_val=sys.argv[3],
    docker_name_val=sys.argv[4]
)

with open("/willow_system/outpost.py", "w") as f:
    f.write(formatted)
EOF

# Write task config json
cat << 'EOF' > /willow_system/willow_task_config.json
{task_config_json}
EOF

python3 /willow_system/format_outpost.py \\
    "{frontend_base_url}" \\
    "{task_secret}" \\
    "{aws_region}" \\
    "{docker_name}"

python3 /willow_system/outpost.py >/willow_system/outpost_stdout 2>/willow_system/outpost_stderr
sudo poweroff
""" # End of user_data_template

def crash(msg):
    print(msg)
    sys.exit(1)

def get_own_secgrp_id(aws_region_name, is_dev):
    secgrp_ids = {
        'us-east-1': {
            'prod': 'sg-0aad80519e7dfdf11',
            'dev': 'sg-0c66b01f7a5354e50',
        },
        'us-west-1': {
            'prod': 'sg-06adc82de8909fe8d',
            'dev': 'sg-0d536e6af88bbfd2e',
        }
    }
    secgrp_id = secgrp_ids.get(aws_region_name, {}).get('dev' if is_dev else 'prod')
    if not secgrp_id:
        crash(f"Error: Security Group ID not found for region '{aws_region_name}'")
    return secgrp_id

def get_own_subnet_id(aws_region_name, is_dev):
    subnet_ids = {
        'us-east-1': {
            'prod': 'subnet-02451aea58112ec36', # prod-IPv6only-taskVMs
            'dev': 'subnet-053a3b5b06a5dfd84',  # dev-IPv6only-taskVMs
        },
        'us-west-1': {
            'prod': 'subnet-019da208300f6fd5e', # prod-west1a-IPv6only-taskVMs
            'dev': 'subnet-019919d496c608d52', # dev-west-IPv6only-taskVMs
        }
    }
    subnet_id = subnet_ids.get(aws_region_name, {}).get('dev' if is_dev else 'prod')
    if not subnet_id:
        crash(f"Error: Subnet ID not found for region '{aws_region_name}' and environment")
    return subnet_id

def connect_ec2_client(use_willows_own_vpc, aws_region_name, customer_aws_account_id, job_id):
    if use_willows_own_vpc:
        return boto3.client('ec2', region_name=aws_region_name, config=Boto3Config(use_dualstack_endpoint = True))

    customer_role_arn = f"arn:aws:iam::{customer_aws_account_id}:role/{ROLE_NAME_IN_CUSTOMER_ACCOUNT}"
    sts_client = boto3.client('sts', region_name=aws_region_name, endpoint_url='https://sts.us-east-1.amazonaws.com')
    job_stuff = job_id.split('-')
    job_date = job_stuff[0]
    job_time = job_stuff[1]
    job_random_str = job_stuff[2]
    assumed_role_object = sts_client.assume_role(
        RoleArn=customer_role_arn,
        RoleSessionName=f'willow-{customer_aws_account_id}-{job_date}-{job_time}-{job_random_str}'
    )
    credentials = assumed_role_object['Credentials']

    return boto3.client('ec2',
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken'],
                        region_name=aws_region_name,
                        config=Boto3Config(use_dualstack_endpoint = True))

def main():
    parser = argparse.ArgumentParser(description="Launch an EC2 instance for a task.")
    parser.add_argument("--task-secret", required=True, help="Secret token for the task.")
    parser.add_argument("--task-config-json", required=True, help="JSON string containing task configuration.")
    parser.add_argument("--aws-region", required=True, help="AWS region to launch the instance in.")
    parser.add_argument("--dev", action="store_true", help="Run in development mode.")
    parser.add_argument("--prod", action="store_true", help="Run in production mode.")
    parser.add_argument("--use-willows-own-vpc", action="store_true", help="The task will be run in Willow's own AWS account.")
    parser.add_argument("--sec-grp", help="AWS security group to launch the instance in. Required if not --use-willows-own-vpc.")
    parser.add_argument("--subnet", help="AWS subnet to launch the instance in. Required if not --use-willows-own-vpc.")
    parser.add_argument("--customer-aws-id", help="Customer's AWS account ID. Required if not --use-willows-own-vpc.")
    parser.add_argument("--ami-id", required=True, help="AWS AMI the VM should be created from.")
    parser.add_argument("--docker-name", required=True, help="Name of Docker image to run in, 'none' for no Docker.")
    parser.add_argument("--instance-type", required=True, help="AWS VM type to use, e.g. t3.micro.")
    parser.add_argument("--frontend-base-url", required=True, help="Base URL for the task to report status.")

    args = parser.parse_args()

    if args.dev and args.prod:
        crash("Error: Cannot specify both --dev and --prod.")
    if not args.dev and not args.prod:
        crash("Error: Must specify either --dev or --prod.")

    is_dev = args.dev
    aws_region_name = args.aws_region

    # NOTE: do not use print(); the caller parses stdout for print(f"{instance_id}"). Use crash().

    # --- Prepare UserData script content ---
    user_data_script = user_data_template.format(
        frontend_base_url=args.frontend_base_url,
        task_secret=args.task_secret,
        task_config_json=args.task_config_json,
        aws_region=aws_region_name,
        docker_name=args.docker_name
    )

    # --- Launch EC2 Instance ---
    # This profile must grant necessary S3 permissions (e.g., AmazonS3FullAccess policy).
    instance_profile_name = "ec2_runner" if args.use_willows_own_vpc else "WillowbenchVMInstanceRole"

    subnet_id = get_own_subnet_id(aws_region_name, is_dev) if args.use_willows_own_vpc else args.subnet
    secgrp_id = get_own_secgrp_id(aws_region_name, is_dev) if args.use_willows_own_vpc else args.sec_grp
    customer_aws_id = args.customer_aws_id if not args.use_willows_own_vpc else '1'

    try:
        config = json.loads(args.task_config_json)
        job_id = config.get('job_id', 'unknown')
        task_name = config.get('task_name', 'unknown')

        ec2 = connect_ec2_client(args.use_willows_own_vpc, aws_region_name, customer_aws_id, job_id)

        devstr = 'DEV' if is_dev else 'willow'
        instance_name = f"{devstr}-task-{job_id}-{task_name}"
        if len(instance_name) > 255:
            crash(f"intended task VM instance name is too long (max 255): {instance_name}")

        # Remember, subnets, route tables, and security groups must be configured for IPv6!
        ec2_run_params = {
            'ImageId': args.ami_id,
            'InstanceType': args.instance_type,
            'MinCount': 1,
            'MaxCount': 1,
            'IamInstanceProfile': {'Name': instance_profile_name},
            'TagSpecifications': [{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': instance_name}]
            }],
            'UserData': user_data_script,
            'InstanceInitiatedShutdownBehavior': 'terminate', # terminate (delete) upon poweroff
            'SecurityGroupIds': [secgrp_id],
            'SubnetId': subnet_id,
            'Ipv6AddressCount': 1,
        }
        # note: keys are regional for some reason; must copy adminfredtest over to each new region
        if args.use_willows_own_vpc or int(customer_aws_id) <= 101177: # 101177 is my test customer
            ec2_run_params['KeyName'] = 'adminfredtest'

        response = ec2.run_instances(**ec2_run_params)

        instance_id = response['Instances'][0]['InstanceId']
        print(f"{instance_id}")

    except Exception as e:
        crash(f"Error launching instance: {e}")

if __name__ == "__main__":
    main()
