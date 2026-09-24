import base64
import datetime as dt
import io
import secrets
from flask import session, render_template, request, redirect, url_for, flash, send_file, jsonify
import boto3
from botocore.config import Config as Boto3Config
from botocore.exceptions import ClientError

import config
import forms
import our_mongo as mdb
import utils
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

def send_verification_email(dest_addr, code):
    ses_client = boto3.client("ses", region_name="us-east-1", config=Boto3Config(use_dualstack_endpoint = True))
    sender_email = "noreply@willowbench.bio"
    subject = "Verify your email address"
    verification_link = f"https://bench.willowbench.bio/set_password?code={code}"

    body_text = f"If you are creating a Willowbench account or resetting a password, please visit this URL to set your password: {verification_link}"
    body_html = f"""<html>
    <body>
      <p>If you are creating a Willowbench account or resetting a password, please click this link to set your password:</p>
      <p><a href="{verification_link}">Set Password</a></p>
      <p>Otherwise, please ignore this email.</p>
    </body>
    </html>
    """

    try:
        ses_client.send_email(
            Destination={ "ToAddresses": [dest_addr] },
            Message={
                "Body": {
                    "Html": { "Charset": "UTF-8", "Data": body_html },
                    "Text": { "Charset": "UTF-8", "Data": body_text },
                },
                "Subject":  { "Charset": "UTF-8", "Data": subject },
            },
            Source=sender_email,
        )
        log_info(f"Verification email sent to {dest_addr}.")
        return True
    except ClientError as e:
        log_error(f"Error sending email to {dest_addr}: {e.response['Error']['Message']}")
    except Exception as e:
        log_error(f"Error sending email to {dest_addr}: {e}")
    return False


def send_admin_ver_email(admin_email, code):
    ses_client = boto3.client("ses", region_name="us-east-1", config=Boto3Config(use_dualstack_endpoint = True))
    sender_email = "noreply@willowbench.bio"
    subject = "Verify your email address"
    verification_link = f"https://bench.willowbench.bio/set_password?code={code}"

    body_text = f"Please visit this URL to set your Willowbench admin account password: {verification_link}"
    body_html = f"""<html>
    <body>
        <p>Please click this link to set your Willowbench admin account password:</p>
        <p><a href="{verification_link}">Set Password</a></p>
        <p>Otherwise, please ignore this email.</p>
    </body>
    </html>
    """

    try:
        ses_client.send_email(
            Destination={ "ToAddresses": [admin_email] },
            Message={
                "Body": {
                    "Html": { "Charset": "UTF-8", "Data": body_html },
                    "Text": { "Charset": "UTF-8", "Data": body_text },
                },
                "Subject":  { "Charset": "UTF-8", "Data": subject },
            },
            Source=sender_email,
        )
        log_info(f'Admin verification code sent to {admin_email}.')
        return True
    except ClientError as e:
        log_error(f"Error sending email to {admin_email}: {e.response['Error']['Message']}")
    except Exception as e:
        log_error(f"Error sending email to {admin_email}: {e}")
    return False


def handle_reset_password_url():
  form = forms.ResetPasswordForm()
  if form.validate_on_submit():
    if not utils.is_valid_email(form.useremail.data):
      flash('Invalid email address.', 'warning')
      return render_template('reset_password.html', form=form, show_nav_links=False)
    if start_verification(form.useremail.data):
      return redirect(url_for('verification_sent'))
  flash('Failed to send verification email.', 'warning')
  return render_template('reset_password.html', form=form, show_nav_links=False)

def start_verification(email_addr, customer_id=None):
    if customer_id is None:
      customer_id = mdb.get_customer_id(email_addr)
      if customer_id is None:
        log_warn(f"Could not find customer_id for email {email_addr} during verification start.")
        return False
    the_code = secrets.token_urlsafe(16)
    expiration = dt.datetime.now() + dt.timedelta(minutes=20000) # about 2 weeks
    mdb.insert_inflight_code(the_code, email_addr, expiration, customer_id, False)
    return send_verification_email(email_addr, the_code)

def start_new_customer_verification(admin_email, customer_id):
  the_code = secrets.token_urlsafe(16)
  expiration = dt.datetime.now() + dt.timedelta(minutes=20000) # about 2 weeks
  mdb.insert_inflight_code(the_code, admin_email, expiration, customer_id, True)
  return send_admin_ver_email(admin_email, the_code)

def consume_valid_inflight_code(code):
  inflight = mdb.find_inflight_code(code)
  if inflight and dt.datetime.now() < inflight['expiration']:
    email = inflight['email']
    customer_id = inflight['customer_id']
    region = mdb.get_customer_region(customer_id)
    create_as_admin = inflight.get('create_as_admin', False)
    mdb.delete_inflight_code(code)
    return email, customer_id, region, create_as_admin
  return None, None, None, None

def handle_finish_reg_or_reset():
  form = forms.SetPasswordForm()
  if request.method == 'GET':
    code = request.args.get('code')
    if code:
      form.hidden_code.data = code
      inflight = mdb.find_inflight_code(code)
      if inflight and dt.datetime.now() < inflight['expiration']:
        return render_template('set_password.html', form=form, show_nav_links=False)
  elif form.validate_on_submit():
    email, customer_id, region, create_as_admin = consume_valid_inflight_code(form.hidden_code.data)
    if email:
      mdb.db_commit_username_and_pw(email, form.password.data.encode('utf-8'), customer_id, region, create_as_admin)
      flash('Password set successfully. You can now log in.', 'success')
      return redirect(url_for('login'))

  flash('Invalid or expired code.', 'danger')
  return redirect(url_for('login'))

def handle_login_url(just_logged_out=False):
  form = forms.LoginForm()
  if form.validate_on_submit():
    if mdb.user_and_pw_match_db(form.useremail.data, form.password.data.encode('utf-8')):
      session['username'] = form.useremail.data
      #flash('Login successful! Welcome ' + form.useremail.data, 'success')
      return redirect(url_for('index'))
    flash('Invalid username or password', 'danger')
  return render_template('login.html', form=form, show_nav_links=False, just_logged_out=just_logged_out)

def handle_account_settings(username):
  user = mdb.find_user(username)
  return render_template('account_settings.html', useremail=username,
                         customer_id=user.get('customer_id'), region=user.get('preferred_region'))

def send_account_reg_invitation(email, customer_id):
  if email and utils.is_valid_email(email):
    if not mdb.find_user(email):
      return start_verification(email, customer_id)
    else:
      log_warn(f"Attempted to invite existing user: {email}")
      flash(f'User {email} already exists.', 'warning')
  elif email:
    flash(f'Invalid email format: {email}', 'danger')
  return False

def serve_vpc_format_script(customer_id_blob, verification_hash, cloud_region):
  if not customer_id_blob:
    return "Missing ?customer_id_blob= parameter.", 400
  try:
    their_name_email_id = base64.b64decode(customer_id_blob.encode()).decode().strip().split('\n')
  except Exception as e:
    log_error(f"Bad customer_id_blob format: {e}")
    return "bad customer_id_blob", 400
  customer_name = their_name_email_id[0]
  admin_email = their_name_email_id[1]
  cust_id_str = their_name_email_id[2]

  if len(cust_id_str) != 6 or not cust_id_str.isdigit() or int(cust_id_str) < 100000 or int(cust_id_str) > 999999:
    log_error(f"Invalid customer ID format received: {cust_id_str}")
    return "Willowbench customer IDs must be 6-digit numbers.", 400

  if not verification_hash:
    return "Missing ?verification= parameter.", 400
  if verification_hash != utils.generate_customer_hash(cust_id_str, config.new_customer_salt):
    log_warn(f"Invalid verification hash for customer ID {cust_id_str}")
    return 'Invalid verification hash', 403

  if not cloud_region:
    return "Missing ?cloud_region= parameter.", 400
  if cloud_region not in ['us-east-1', 'us-west-1']:
    log_warn(f"Unsupported cloud region requested: {cloud_region}")
    return "only us-east-1 and us-west-1 are currently supported", 400

  try:
    with open('../accounts/willowbench_format_new_customer_aws_vpc.py', 'r') as f:
      file_contents = f.read()
      file_contents = file_contents.replace('THEIR_CUSTOMER_ID_GOES_HERE', cust_id_str)
      file_contents = file_contents.replace('THEIR_REGION_GOES_HERE', cloud_region)
      file_contents = file_contents.replace('VERIFICATION_HASH_GOES_HERE', verification_hash)
      file_contents = file_contents.replace('THEIR_CUSTOMER_NAME_HERE', customer_name)
      file_contents = file_contents.replace('THEIR_ADMIN_EMAIL_HERE', admin_email)

    return send_file(io.BytesIO(file_contents.encode('utf-8')),
                     as_attachment=True,
                     download_name='willowbench_format_new_customer_aws_vpc.py',
                     mimetype='text/x-python')

  except FileNotFoundError:
    log_error("willowbench_format_new_customer_aws_vpc.py template file not found.")
    return "willowbench_format_new_customer_aws_vpc.py template file not found.", 500
  except Exception as e:
    log_error(f"An error occurred serving VPC format script: {e}")
    return f"An error occurred: {e}", 500

def receive_aws_config(data):
  if not data:
      log_error("Received empty JSON data in receive_aws_config.")
      return jsonify({'status': 'error', 'message': 'Invalid JSON data'}), 400

  customer_id = int(data.get('willowbench_customer_id'))
  verification_hash = data.get('verification_hash')

  if not customer_id:
      log_error("Missing customer ID in received AWS config.")
      return jsonify({'status': 'error', 'message': 'Missing customer ID'}), 400
  if not verification_hash:
      log_error("Missing verification hash in received AWS config.")
      return jsonify({'status': 'error', 'message': 'Missing verification hash'}), 400
  if verification_hash != utils.generate_customer_hash(str(customer_id), config.new_customer_salt):
      log_warn(f"Invalid verification hash received for customer ID {customer_id} in receive_aws_config.")
      return jsonify({'status': 'error', 'message': 'Invalid verification hash'}), 403

  aws_id = data.get('customer_aws_account_id')
  subnet_id = data.get('subnet_id')
  security_group_id = data.get('security_group_id')
  cloud_region = data.get('aws_region')
  customer_name = data.get('customer_name')
  admin_email = data.get('admin_email')
  if new_customer_insert_mongo_and_email(customer_id, customer_name, 'subscription',
                                         cloud_region, admin_email, aws_id, subnet_id,
                                         security_group_id, str(data)):
    return 'ok'
  log_error(f"new_customer_insert_mongo_and_email failed for customer ID {customer_id}.")
  return jsonify({'status': 'error', 'message': 'Unknown internal server error'}), 500


def new_customer_insert_mongo_and_email(customer_id, customer_name, billing_type, cloud_region,
                                        admin_email, aws_id, subnet_id, security_group_id, config_dump):
  mdb.insert_new_customer(customer_id, customer_name, billing_type, cloud_region,
                          admin_email, aws_id, subnet_id, security_group_id, config_dump)
  log_info(f'Customer {customer_name} (Willowbench ID {customer_id}) inserted into mongo customers.')

  if start_new_customer_verification(admin_email, customer_id):
      log_info(f"Added customer {customer_name} to mongo customers collection, and sent a registration email to {admin_email}. They should be ready to use the system.")
      return True
  log_error(f"FAILED to send admin registration/verification email to {admin_email} for {customer_name} (Willowbench ID {customer_id})")
  return False


def handle_admin_console(username):
  user = mdb.find_user(username)
  if not user or not user.get('is_admin'):
    log_warn(f"Non-admin user {username} attempted to access admin console.")
    return 'you are not an admin', 403

  customer_id = user.get('customer_id')
  if not customer_id:
      log_error(f"Could not determine customer ID for admin user {username}.")
      return 'Could not determine customer ID for this admin.', 400

  customer_details = mdb.get_customer_details(customer_id)
  if not customer_details:
      log_error(f"Could not find customer details for ID: {customer_id} for admin user {username}.")
      return f'Could not find customer details for ID: {customer_id}', 404

  invite_form = forms.InviteUsersForm()
  purchase_credits_form = forms.PurchaseCreditsForm()

  if invite_form.validate_on_submit():
    invited_count = 0
    for entry in invite_form.emails.entries:
      if send_account_reg_invitation(entry.form.email.data, customer_id):
        invited_count += 1
    if invited_count == 1:
      flash('Sent an invitation.', 'info')
    elif invited_count > 1:
      flash(f'Sent {invited_count} invitations.', 'info')

    # Redirect to GET to clear form and show messages
    return redirect(url_for('admin_console'))

  # Handle GET request or form validation failure
  return render_template('admin.html', useremail=session['username'],
                         customer_id=customer_id,
                         region=user.get('preferred_region'),
                         billing_type=customer_details.get('billing_type'),
                         credits_balance=customer_details.get('credits_balance', 0),
                         invite_form=invite_form,
                         purchase_credits_form=purchase_credits_form) # Pass the new form


from functools import wraps
def login_required(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if 'username' not in session or session['username'] == '':
      return redirect(url_for('login'))

    user = mdb.find_user(session['username'])
    if user is None or user.get('deactivated'):
      session.clear()
      return redirect(url_for('login'))

    return f(*args, **kwargs)
  return decorated_function
