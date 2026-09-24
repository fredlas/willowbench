import datetime as dt
import hashlib
import random
import re
import string
import sys

import boto3
from botocore.config import Config as Boto3Config
from botocore.exceptions import ClientError

def log_error(err_msg, job_id="none"):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_str = f" [job_id={job_id}]" if job_id != "none" else ""
  print(f"{current_time} [frnt][E] {err_msg}{job_str}")

def log_warn(err_msg, job_id="none"):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_str = f" [job_id={job_id}]" if job_id != "none" else ""
  print(f"{current_time} [frnt][W] {err_msg}{job_str}")

def log_info(err_msg, job_id="none"):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_str = f" [job_id={job_id}]" if job_id != "none" else ""
  print(f"{current_time} [frnt][I] {err_msg}{job_str}")

def log_debug(err_msg, job_id="none"):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_str = f" [job_id={job_id}]" if job_id != "none" else ""
  print(f"{current_time} [frnt][D] {err_msg}{job_str}")

def crash(err_msg):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  print(f"{current_time} [frnt][F] {err_msg}")
  sys.exit(1)

def is_valid_email(email):
  pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
  return re.fullmatch(pattern, email) is not None

def send_contact_notification_email(name, email, subject, message, plan_interest):
    ses_client = boto3.client("ses", region_name="us-east-1", config=Boto3Config(use_dualstack_endpoint = True))
    sender_email = "noreply@willowbench.bio"
    recipient_email = "fredlas@willowbench.bio"
    email_subject = "New Contact Form Submission, hooray"

    body_text = f"""
New contact form submission received:

Name: {name}
Email: {email}
Subject: {subject}
Plan Interest: {plan_interest}

Message:
{message}
"""

    body_html = f"""<html>
    <body>
      <p>New contact form submission received:</p>
      <p><b>Name:</b> {name}</p>
      <p><b>Email:</b> {email}</p>
      <p><b>Subject:</b> {subject}</p>
      <p><b>Plan Interest:</b> {plan_interest}</p>
      <p><b>Message:</b></p>
      <pre>{message}</pre>
    </body>
    </html>
    """

    try:
        ses_client.send_email(
            Destination={ "ToAddresses": [recipient_email] },
            Message={
                "Body": {
                    "Html": { "Charset": "UTF-8", "Data": body_html },
                    "Text": { "Charset": "UTF-8", "Data": body_text },
                },
                "Subject":  { "Charset": "UTF-8", "Data": email_subject },
            },
            Source=sender_email,
        )
        log_info(f"Sent contact form notification email to {recipient_email}")
        return True
    except ClientError as e:
        log_error(f"Error sending contact form notification email: {e.response['Error']['Message']}")
    except Exception as e:
        log_error(f"Error sending contact form notification email: {e}")
    return False


# format: yyyymmdd-hhmmss-rrrrrr-workflowname
# where hhmmss is UTC, rrrrrr is random characters a-zA-Z._-
def generate_job_id(workflow_name):
    now_utc = dt.datetime.utcnow()
    date_str = now_utc.strftime("%Y%m%d")
    time_str = now_utc.strftime("%H%M%S")
    allowed_chars = string.ascii_letters + string.digits + "_"
    random_chars = ''.join(random.choices(allowed_chars, k=6))
    cleaned_workflow_name = ''.join(c if c in allowed_chars else 'X' for c in workflow_name)
    return f"{date_str}-{time_str}-{random_chars}-{cleaned_workflow_name}"

def generate_customer_hash(customer_id, salt):
    """Generate a hash for customer ID verification"""
    # This salt is kind of sensitive... if someone had it, they could construct a valid access to
    # /willowbench_format_new_customer_aws_vpc.py and get themselves into the system.
    to_hash = f"{salt}:{customer_id}"
    return hashlib.sha256(to_hash.encode()).hexdigest()

def record_message_from_www_contact(name, email, subject, message, plan_interest, _x_forwarded_for):
    if not email:
      return False

    if len(name) > 200 or len(email) > 255 or len(subject) > 1000 or len(message) > 20000 or len(plan_interest) > 100:
      return False

    send_contact_notification_email(name, email, subject, message, plan_interest)
    return True
