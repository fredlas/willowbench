import json
import sys
import requests

try:
    import boto3
except ImportError:
    print("You need Amazon's 'boto3' Python package installed to run this script")
    sys.exit(1)

YOUR_WILLOWBENCH_CUSTOMER_ID = "THEIR_CUSTOMER_ID_GOES_HERE"
WILLOWBENCH_CENTRAL_AWS_ACCOUNT_ID = "911167923294"
aws_region = "THEIR_REGION_GOES_HERE"
VERIFICATION_HASH = "VERIFICATION_HASH_GOES_HERE"
CUSTOMER_NAME = "THEIR_CUSTOMER_NAME_HERE"
ADMIN_EMAIL = "THEIR_ADMIN_EMAIL_HERE"

def crash(service_name, operation_name, error):
    print(f"\n\n\nERROR: Failed during {service_name} {operation_name}.")
    print(f"Details: {error}")
    print("\nPlease provide this error message to Willowbench support.")
    sys.exit(1)

def print_credential_help_and_exit():
    message = """
ERROR: AWS credentials not found or invalid.
This script requires valid AWS credentials with sufficient permissions to create the necessary resources.

Please ensure your AWS credentials are configured correctly. Recommended approach: re-run this script like:

` AWS_ACCESS_KEY_ID="ABCDE12345" AWS_SECRET_ACCESS_KEY="abcdefgh123456789" python3 willowbench_format_new_customer_aws_vpc.py`

(The leading space keeps these sensitive values out of your bash history)

If you don't know these values, log into AWS, click your account name in the top right corner -> Security credentials -> access keys -> create access key. AWS_ACCESS_KEY_ID is "Access key", AWS_SECRET_ACCESS_KEY is "Secret access key".
"""
    print(message)
    sys.exit(1)

if not (aws_region == 'us-east-1' or aws_region == 'us-west-1'):
    print("ERROR: Only AWS regions us-east-1 and us-west-1 are currently supported.")
    sys.exit(1)

if len(YOUR_WILLOWBENCH_CUSTOMER_ID) != 6 or not YOUR_WILLOWBENCH_CUSTOMER_ID.isdigit() or int(YOUR_WILLOWBENCH_CUSTOMER_ID) < 100000 or int(YOUR_WILLOWBENCH_CUSTOMER_ID) > 999999:
    print("ERROR: it looks like Willowbench didn't properly fill YOUR_WILLOWBENCH_CUSTOMER_ID in this copy of the script. Please let us know.")

try:
    try:
        print("\nPerforming initial AWS credential and connectivity check...")
        sts_identity = boto3.client('sts', region_name=aws_region).get_caller_identity()
        sts_identity_arn = sts_identity['Arn']
        customer_aws_account_id = sts_identity['Account']
        print("Looks good; successfully connected to AWS STS.")

        ec2_client = boto3.client('ec2', region_name=aws_region)
        s3_client = boto3.client('s3', region_name=aws_region)
        iam_client = boto3.client('iam', region_name=aws_region)
    except Exception as e: # Catch any other unexpected error during the check
        print(f"\nERROR: error occurred during the credential/connectivity check: {e}\n\n\n")
        print_credential_help_and_exit()

    print(f"\nYour organization's name: {CUSTOMER_NAME}")
    print(f"Your admin email: {ADMIN_EMAIL}")
    print(f"Your Willowbench customer ID: {YOUR_WILLOWBENCH_CUSTOMER_ID}")
    print(f"Your AWS account ID: {customer_aws_account_id}")
    print(f"Configuring delegation access for Willowbench's AWS account (ID: {WILLOWBENCH_CENTRAL_AWS_ACCOUNT_ID})")
    print(f"Starting resource creation in region: {aws_region}\n")
    config_to_report = {'willowbench_customer_id': YOUR_WILLOWBENCH_CUSTOMER_ID,
                        'aws_region': aws_region,
                        'admin_email': ADMIN_EMAIL,
                        'customer_name': CUSTOMER_NAME,
                        'customer_aws_account_id': customer_aws_account_id}



    # 1. Create VPC
    print("Creating VPC 'willowbench-tasks'...")
    vpc_response = ec2_client.create_vpc(
        CidrBlock='10.0.0.0/16', # Standard private IPv4 range
        AmazonProvidedIpv6CidrBlock=True,
        TagSpecifications=[{'ResourceType': 'vpc', 'Tags': [{'Key': 'Name', 'Value': 'willowbench-tasks'}]}]
    )
    vpc_id = vpc_response['Vpc']['VpcId']
    config_to_report['vpc_id'] = vpc_id
    print(f"VPC created: {vpc_id}")

    vpc_waiter = ec2_client.get_waiter('vpc_available')
    vpc_waiter.wait(VpcIds=[vpc_id])

    # Enable DNS hostnames and DNS support for the VPC
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})

    # Describe VPC to get the IPv6 CIDR block
    vpc_description = ec2_client.describe_vpcs(VpcIds=[vpc_id])
    vpc_ipv6_cidr_block = None
    for assoc in vpc_description['Vpcs'][0].get('Ipv6CidrBlockAssociationSet', []):
        if assoc.get('Ipv6CidrBlockState', {}).get('State') == 'associated':
            vpc_ipv6_cidr_block = assoc['Ipv6CidrBlock']
            break
    if not vpc_ipv6_cidr_block:
        crash("EC2", "DescribeVpc (IPv6 CIDR)", "Could not retrieve IPv6 CIDR block for VPC.")
    print(f"VPC IPv6 CIDR block: {vpc_ipv6_cidr_block}")
    # Assuming /56, create a /64 for the subnet.
    # e.g. if vpc_ipv6_cidr_block is "2600:1f18:xxxx:yy00::/56", subnet_ipv6_cidr will be "2600:1f18:xxxx:yy00::/64"
    subnet_ipv6_cidr = vpc_ipv6_cidr_block.split('/')[0] + "/64"



    # 2. Create Internet Gateway and attach to VPC
    print("Creating Internet Gateway...")
    igw_response = ec2_client.create_internet_gateway(
        TagSpecifications=[{'ResourceType': 'internet-gateway',
                            'Tags': [{'Key':   'Name',
                                      'Value': 'willowbench-igw'}]}]
    )
    igw_id = igw_response['InternetGateway']['InternetGatewayId']
    config_to_report['internet_gateway_id'] = igw_id
    print(f"Internet Gateway created: {igw_id}")

    print(f"Attaching Internet Gateway {igw_id} to VPC {vpc_id}...")
    ec2_client.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    print("Internet Gateway attached.")



    # 3. Create Subnet (IPv6 enabled)
    print("Creating Subnet...")
    subnet_response = ec2_client.create_subnet(
        VpcId=vpc_id,
        CidrBlock='10.0.1.0/24', # IPv4 CIDR for the subnet
        Ipv6CidrBlock=subnet_ipv6_cidr,
        AvailabilityZone=f"{aws_region}a",
        TagSpecifications=[{'ResourceType': 'subnet', 'Tags': [{'Key': 'Name', 'Value': 'willowbench-tasks-subnet'}]}]
    )
    subnet_id = subnet_response['Subnet']['SubnetId']
    config_to_report['subnet_id'] = subnet_id
    print(f"Subnet created: {subnet_id}")

    ec2_client.modify_subnet_attribute(SubnetId=subnet_id, AssignIpv6AddressOnCreation={'Value': True})
    ec2_client.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={'Value': False})
    print(f"Subnet {subnet_id} configured to auto-assign IPv6 addresses.")



    # 4. Create Route Table and Routes
    print("Creating Route Table...")
    rt_response = ec2_client.create_route_table(
        VpcId=vpc_id,
        TagSpecifications=[{'ResourceType': 'route-table',
                            'Tags': [{'Key':   'Name',
                                      'Value': 'willowbench-tasks-rt'}]}]
    )
    rt_id = rt_response['RouteTable']['RouteTableId']
    config_to_report['route_table_id'] = rt_id
    print(f"Route Table created: {rt_id}")

    print(f"Creating route for IPv6 (::/0) to Internet Gateway {igw_id}...")
    ec2_client.create_route(RouteTableId=rt_id, DestinationIpv6CidrBlock='::/0', GatewayId=igw_id)
    print("IPv6 route created.")

    print(f"Associating Route Table {rt_id} with Subnet {subnet_id}...")
    ec2_client.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)
    print("Route Table associated.")



    # 5. Create Security Group
    print("Creating Security Group 'willowbench-task-sg'...")
    sg_response = ec2_client.create_security_group(
        GroupName='willowbench-task-sg',
        Description='Security group for Willowbench task VMs',
        VpcId=vpc_id,
        TagSpecifications=[{'ResourceType': 'security-group',
                            'Tags': [{'Key':   'Name',
                                      'Value': 'willowbench-task-sg'}]}]
    )
    sg_id = sg_response['GroupId']
    config_to_report['security_group_id'] = sg_id
    print(f"Security Group created: {sg_id}")
    print("(the security group has default permissions: all egress allowed, no ingress allowed)")



    # 6. Create S3 Bucket
    s3_bucket_name = f"willowbench-tasks-{YOUR_WILLOWBENCH_CUSTOMER_ID}-{aws_region}"
    config_to_report['s3_bucket_name'] = s3_bucket_name
    print(f"Creating S3 bucket '{s3_bucket_name}'...")
    try:
        # NOTE: yes, this clunkiness is necessary. AWS decrees that you not specify this param if
        #       it would be us-east-1, in fact chooses to make it fail, for "historical reasons".
        if aws_region == 'us-east-1':
            s3_client.create_bucket(Bucket=s3_bucket_name, ObjectOwnership='ObjectWriter')
        else:
            s3_client.create_bucket(Bucket=s3_bucket_name,
                                    CreateBucketConfiguration={'LocationConstraint': aws_region},
                                    ObjectOwnership='ObjectWriter')
        print(f"S3 bucket '{s3_bucket_name}' created with ACLs enabled (ObjectWriter).")

        # Apply bucket policy to grant Willowbench account access
        willowbench_principal_arn = f"arn:aws:iam::{WILLOWBENCH_CENTRAL_AWS_ACCOUNT_ID}:root"
        bucket_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowWillowbenchAccountAccess",
                    "Effect": "Allow",
                    "Principal": {"AWS": willowbench_principal_arn},
                    "Action": [
                        "s3:ListBucket",
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:GetObjectAcl",
                        "s3:PutObjectAcl"
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{s3_bucket_name}",
                        f"arn:aws:s3:::{s3_bucket_name}/*"
                    ]
                }
            ]
        }
        s3_client.put_bucket_policy(Bucket=s3_bucket_name, Policy=json.dumps(bucket_policy))
        print(f"Bucket policy applied to '{s3_bucket_name}' granting access to {willowbench_principal_arn}.")

    except Exception as e:
        if "BucketAlreadyOwnedByYou" in str(e):
            print(f"S3 bucket '{s3_bucket_name}' already exists and is owned by you. Proceeding.")
        else:
            crash("S3", f"CreateBucket/Policy {s3_bucket_name}", e)



    # 7. Create IAM Role for EC2 Instance Profile (WillowbenchVMInstanceRole)
    vm_instance_role_name = "WillowbenchVMInstanceRole"
    print(f"Creating IAM role '{vm_instance_role_name}' for EC2 instance profile...")
    assume_role_policy_ec2 = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }
    try:
        vm_role_response = iam_client.create_role(
            RoleName=vm_instance_role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy_ec2),
            Description="Role for Willowbench task VMs to access S3 and other services."
        )
        vm_role_arn = vm_role_response['Role']['Arn']
        config_to_report['vm_instance_role_arn'] = vm_role_arn
        config_to_report['vm_instance_role_name'] = vm_instance_role_name # for output clarity
        print(f"IAM role '{vm_instance_role_name}' created: {vm_role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"IAM role '{vm_instance_role_name}' already exists. Using existing role.")
        vm_role_arn = f"arn:aws:iam::{customer_aws_account_id}:role/{vm_instance_role_name}"
        config_to_report['vm_instance_role_arn'] = vm_role_arn
        config_to_report['vm_instance_role_name'] = vm_instance_role_name

    print(f"Attaching 'AmazonS3FullAccess' policy to role '{vm_instance_role_name}'...")
    iam_client.attach_role_policy(
        RoleName=vm_instance_role_name,
        PolicyArn='arn:aws:iam::aws:policy/AmazonS3FullAccess'
    )
    print("'AmazonS3FullAccess' policy attached.")



    # 8. Create IAM Instance Profile for the VM role
    instance_profile_name = vm_instance_role_name # Convention: just use same name as the role
    print(f"Creating IAM instance profile '{instance_profile_name}'...")
    try:
        ip_response = iam_client.create_instance_profile(InstanceProfileName=instance_profile_name)
        config_to_report['instance_profile_arn'] = ip_response['InstanceProfile']['Arn']
        config_to_report['instance_profile_name'] = instance_profile_name
        print(f"Instance profile '{instance_profile_name}' created.")
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"Instance profile '{instance_profile_name}' already exists. Using existing.")
        config_to_report['instance_profile_arn'] = f"arn:aws:iam::{customer_aws_account_id}:instance-profile/{instance_profile_name}"
        config_to_report['instance_profile_name'] = instance_profile_name

    # Add role to instance profile. First remove - only relevant for development (multiple runs on
    # same account) but harmless in production.
    try:
        iam_client.remove_role_from_instance_profile(InstanceProfileName=instance_profile_name,
                                                     RoleName=vm_instance_role_name)
    except Exception as e:
        pass # expected to fail

    try:
        iam_client.add_role_to_instance_profile(InstanceProfileName=instance_profile_name,
                                                RoleName=vm_instance_role_name)
        print(f"Role '{vm_instance_role_name}' added to instance profile '{instance_profile_name}'.")
    except Exception as e:
        crash("IAM", f"AddRoleToInstanceProfile {vm_instance_role_name} to {instance_profile_name}", e)



    # 9. Create IAM Role for Willowbench Delegation (WillowbenchTaskLauncherRole)
    delegation_role_name = "WillowbenchTaskLauncherRole"
    print(f"Creating IAM delegation role '{delegation_role_name}' for Willowbench account...")
    willowbench_principal_arn = f"arn:aws:iam::{WILLOWBENCH_CENTRAL_AWS_ACCOUNT_ID}:root"
    trust_policy_willowbench = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": willowbench_principal_arn},
            "Action": "sts:AssumeRole"
        }]
    }
    try:
        delegation_role_response = iam_client.create_role(
            RoleName=delegation_role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy_willowbench),
            Description="Role for Willowbench platform to launch EC2 task VMs."
        )
        delegation_role_arn = delegation_role_response['Role']['Arn']
        config_to_report['delegation_role_arn'] = delegation_role_arn
        print(f"IAM delegation role '{delegation_role_name}' created: {delegation_role_arn}")
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"IAM role '{delegation_role_name}' already exists. Using existing role.")
        # Ensure trust policy is up-to-date
        iam_client.update_assume_role_policy(
            RoleName=delegation_role_name,
            PolicyDocument=json.dumps(trust_policy_willowbench)
        )
        print(f"Updated trust policy for existing role '{delegation_role_name}'.")
        delegation_role_arn = f"arn:aws:iam::{customer_aws_account_id}:role/{delegation_role_name}"
        config_to_report['delegation_role_arn'] = delegation_role_arn

    permissions_policy_willowbench = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ec2:RunInstances", "ec2:CreateTags", "ec2:TerminateInstances",
                           "ec2:DescribeInstances", "ec2:DescribeImages", "ec2:DescribeInstanceTypes",
                           "ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "ec2:DescribeVpcs",
                           "ec2:DescribeKeyPairs"],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": vm_role_arn,
                "Condition": {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}}
            }
        ]
    }
    print(f"Attaching permissions policy to role '{delegation_role_name}'...")
    iam_client.put_role_policy(
        RoleName=delegation_role_name,
        PolicyName='WillowbenchTaskLauncherPermissions',
        PolicyDocument=json.dumps(permissions_policy_willowbench)
    )
    print("Permissions policy attached.")
    print(f"All resources have been created in region '{aws_region}'.")


    # 10. Output created resource identifiers and submit to Willowbench
    print("\n\n\n\n--- sending this config to Willowbench: ---")
    for key, value in config_to_report.items():
        print(f"{key}: {value}")

    print("-----------------------------------------------------")
    print("\nSubmitting configuration to Willowbench...")
    config_to_report["verification_hash"] = VERIFICATION_HASH
    try:
        response = requests.post(
            "https://bench.willowbench.bio/submit_aws_config",
            json=config_to_report,
            timeout=30
        )
        if response.status_code == 200:
            print("Configuration successfully submitted to Willowbench!")
            print("\n--- AWS Infrastructure Setup for Willowbench Successful! ---\n")
            print("The admin user will receive an email shortly to set a password for accessing Willowbench.")
            print("It is now safe to delete this script.")
        else:
            print(f"Error submitting configuration: {response.status_code}")
            print(f"Response: {response.text}\n\n")
            print("\nPlease provide the 'sending this config to Willowbench' section to Willowbench support.")
    except Exception as e:
        print(f"Error submitting configuration: {e}\n\n")
        print("\nPlease provide the 'sending this config to Willowbench' section to Willowbench support.")

except boto3.exceptions.Boto3Error as e:
    crash("AWS API Call", "General Boto3", e)
except Exception as e:
    print(f"AN UNEXPECTED ERROR OCCURRED: {e}")
    print("Script execution failed. Provide this full error message to Willowbench support.")
    import traceback
    traceback.print_exc()
    sys.exit(1)
