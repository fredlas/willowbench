from flask import Flask, session, render_template, request, redirect, url_for, flash, jsonify, Response
import argparse
import flask_wtf
import os
import requests

import account
from account import login_required
import config
import workflows as wf
import job_status
import money
import utils
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

from dotenv import load_dotenv
load_dotenv("/home/admin/willow_fe_env")
willow_flask_secret_key = os.environ.get('WILLOW_FRONTEND_FLASK_SECRET_KEY')
if not willow_flask_secret_key:
    utils.crash("WILLOW_FRONTEND_FLASK_SECRET_KEY environment variable not set!")

stripe_publishable_key = os.environ.get('STRIPE_PUBLISHABLE_KEY')
if not stripe_publishable_key:
    utils.crash("STRIPE_PUBLISHABLE_KEY environment variable not set!")

app = Flask(__name__)
app.secret_key = willow_flask_secret_key
app.config['WTF_CSRF_TIME_LIMIT'] = 3600000 # 1000 hours
csrf = flask_wtf.csrf.CSRFProtect(app)

# TODO is this useful? either remove this, or we might want to do similar with other global consts
@app.context_processor
def inject_stripe_publishable_key():
    """Injects the Stripe publishable key into all templates."""
    return dict(stripe_publishable_key=stripe_publishable_key)





# BEGIN misc


@app.errorhandler(404)
def page_not_found(_e):
  return render_template('generic_404.html'), 404

# comes from the www site contact page
@app.route('/contact', methods=['POST'])
@csrf.exempt
def contact_handler():
  if request.method == 'POST':
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if utils.record_message_from_www_contact(request.form.get('name'), request.form.get('email'),
                                             request.form.get('subject'), request.form.get('message'),
                                             request.form.get('plan_interest'), x_forwarded_for):
      return redirect("https://willowbench.bio/contact_success")
  return redirect("https://willowbench.bio/contact_failure")

@app.route('/')
@login_required
def index():
  return redirect(url_for('workflows_menu_handler'))

@app.route('/health')
def healthcheck():
  return 'ok'

@app.route('/outpost_template.py', methods=['GET'])
def outpost_template_handler():
  current_dir = os.path.dirname(os.path.abspath(__file__))
  template_path = os.path.join(current_dir, '..', 'utils', 'outpost_template.py')
  try:
    with open(template_path, 'r') as f:
      template = f.read()
  except FileNotFoundError:
    return Response("Error: outpost_template.py not found.", mimetype='text/plain', status=404)
  except IOError:
    return Response("Error: Could not read outpost_template.py.", mimetype='text/plain', status=500)
  return Response(template, mimetype='text/plain')

# END misc




# BEGIN jobs and workflows

@app.route('/workflows')
@login_required
def workflows_menu_handler():
  return wf.workflows_library_handler(session['username'])

@app.route('/workflows/<path:workflow_name>', methods=['GET', 'POST'])
@login_required
def workflows(workflow_name):
  return wf.workflows_url_handler(workflow_name, session['username'])


@app.route('/api/workflows/<path:workflow_name>/submit', methods=['POST'])
@csrf.exempt
def api_workflow_submit(workflow_name):
  auth_header = request.headers.get('Authorization')
  if not auth_header or not auth_header.startswith('Bearer '):
    return jsonify({"status": "error", "message": "Authorization header with Bearer token is required"}), 401

  token = auth_header.split(' ')[1]
  if not token:
    return jsonify({"status": "error", "message": "Empty API token"}), 403

  # TODO TODO HACK HACK TODO TODO TODO TODO TODO TODO TODO TODO
  admin_email = 'a@a.a'
  if token != 'supersqewcretqmduxieruj84mfw34hjc3447fdddjxnvkuse563iimdfwd65348':
    return jsonify({"status": "error", "message": "nope"}), 403
  # customer = mdb.find_customer_by_api_token(token.encode('utf-8'))
  # if not customer:
  #   return jsonify({"status": "error", "message": "Invalid API token"}), 403
  # admin_email = customer['admin_email']

  return wf.handle_api_workflow_submission(workflow_name, admin_email)


@app.route('/jobs')
@login_required
def jobs_menu_handler():
  return job_status.jobs_status_page_handler(session['username'])

@app.route('/example')
def example_job_page_handler():
  log_info(f"Access to /example from IP: {request.remote_addr}")
  return job_status.example_job_page_handler()

@app.route('/job/<job_id>')
@login_required
def job_page_handler(job_id):
  return job_status.job_page_handler_logic(session['username'], job_id)

@app.route('/job/<job_id>/download/<inputs_or_outputs>/<key>')
@login_required
def io_download_handler(job_id, inputs_or_outputs, key):
  return job_status.download_io_handler(session['username'], job_id, inputs_or_outputs, key)

@app.route('/example/download/<inputs_or_outputs>/<key>')
def example_io_download_handler(inputs_or_outputs, key):
  log_info(f"Access to /example/download/{inputs_or_outputs}/{key} from IP: {request.remote_addr}")
  return job_status.download_io_handler_public(inputs_or_outputs, key)

@app.route('/job/<job_id>/view/<inputs_or_outputs>/<key>')
@login_required
def io_view_handler(job_id, inputs_or_outputs, key):
  return job_status.view_io_handler(session['username'], job_id, inputs_or_outputs, key)

@app.route('/example/view/<inputs_or_outputs>/<key>')
def example_io_view_handler(inputs_or_outputs, key):
  return job_status.view_io_handler_public(inputs_or_outputs, key)

@app.route('/job/<job_id>/update_comment', methods=['POST'])
@login_required
def job_comment_handler(job_id):
  return job_status.update_job_comment_handler(session['username'], job_id)

@app.route('/job/<job_id>/update_nickname', methods=['POST'])
@login_required
def job_nickname_handler(job_id):
  return job_status.update_job_nickname_handler(session['username'], job_id)

@app.route('/cancel_workflow', methods=['POST'])
@login_required
def cancel_job_handler():
    # Get job_id from form data instead of query params
    job_id = request.form.get('job_id')
    if not job_id:
        flash('Error: No job ID provided for cancellation.', 'danger')
        return redirect(url_for('jobs_menu_handler'))
    return job_status.cancel_job(session['username'], job_id)

@app.route('/job/<job_id>/resubmit', methods=['POST'])
@login_required
def resubmit_job_handler(job_id):
  return job_status.resubmit_job_handler(session['username'], job_id)

# END jobs and workflows


# BEGIN core API proxy endpoints
def _check_field(req_json, field, job_id_for_log=None):
  value = req_json.get(field)
  if not value:
    log_error(f"/task/task_done with no {field} field", job_id_for_log)
    raise ValueError(f"request needs a {field} field")
  return value

@app.route('/task/task_done', methods=['POST'])
@csrf.exempt
def proxy_task_done():
  try:
    job_id = _check_field(request.json, 'job_id')
    task_name = _check_field(request.json, 'task_name', job_id)

    backend_port = config.get_backend_port()
    task_done_url = f"http://[::1]:{backend_port}/task/task_done"
    log_info(f'forwarding a /task/task_done for task {task_name}', job_id)
    response = requests.post(task_done_url, json=request.json, timeout=10)
    return Response(response.content, status=response.status_code, headers=dict(response.headers))
  except ValueError as e:
    return jsonify({"status": "error", "message": e.args[0]}), 400
  except requests.exceptions.RequestException as e:
    log_error(f"Error proxying task_done to backend: {e}", job_id)
    return jsonify({"status": "error", "message": str(e)}), 500

# END core API proxy endpoints


# BEGIN account

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
  return account.handle_reset_password_url()

@app.route('/verification_sent', methods=['GET'])
def verification_sent():
  return render_template('verification_sent.html', show_nav_links=False)

@app.route('/set_password', methods=['GET', 'POST'])
def set_password():
  return account.handle_finish_reg_or_reset()

@app.route('/login', methods=['GET', 'POST'])
def login():
  return account.handle_login_url()

@app.route('/logout', methods=['GET', 'POST'])
def logout():
  session.clear()
  return account.handle_login_url(just_logged_out=True)

@app.route('/account_settings')
@login_required
def account_settings():
  return account.handle_account_settings(session['username'])

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_console():
  return account.handle_admin_console(session['username'])

@app.route('/edit_library_filter', methods=['GET', 'POST'])
@login_required
def edit_library_filter_handler():
  return wf.library_edit_handler(session['username'])

# END account


# BEGIN Billing Routes

@app.route('/billing/update_subscription', methods=['POST'])
@login_required
def update_subscription_billing_handler():
    return money.handle_update_subscription_request(session['username'])

@app.route('/billing/purchase_credits', methods=['POST'])
@login_required
def purchase_credits_handler():
    return money.handle_purchase_credits_request(session['username'])

@app.route('/billing/success')
@login_required
def billing_success_handler():
    # This route is hit after a successful Stripe Checkout session
    # You can optionally retrieve session details using the session_id query parameter
    # session_id = request.args.get('session_id')
    flash('Payment successful! Your credits balance will be updated shortly.', 'success')
    # NOTE: do NOT add credits here. that is done in handle_webhook_completed().
    return redirect(url_for('admin_console'))

@app.route('/billing/cancel')
@login_required
def billing_cancel_handler():
    # This route is hit if the user cancels the Stripe Checkout session
    flash('Checkout cancelled (and payment not submitted).', 'info')
    return redirect(url_for('admin_console'))

@app.route('/stripe_webhook', methods=['POST'])
@csrf.exempt
def stripe_webhook_handler():
    # This endpoint receives events from Stripe
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    # Call the money module to handle the webhook event
    return money.handle_stripe_webhook(payload, sig_header)

# END Billing Routes


# BEGIN delegation setup

@app.route('/willowbench_format_new_customer_aws_vpc.py', methods=['GET'])
def aws_vpc_format_script_handler():
  return account.serve_vpc_format_script(request.args.get('customer_id_blob'),
                                         request.args.get('verification'),
                                         request.args.get('cloud_region'))

@app.route('/submit_aws_config', methods=['POST'])
@csrf.exempt
def submit_aws_config_handler():
    try:
        return account.receive_aws_config(request.get_json())
    except Exception as e:
        log_error(f"Error in submit_aws_config_handler: {e}")
        return jsonify({'status': 'error', 'message': f'Internal server error: {e}'}), 500

# END delegation setup

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Willowbench webapp frontend')
  parser.add_argument('--port', type=int, default=8008, help='Port to run the server on (default: 8008)')
  parser.add_argument('--debug', action='store_true', help='Run the server in debug mode (default: False)')
  args = parser.parse_args()
  log_info('now running DEBUG Flask server')
  app.run(debug=args.debug, port=args.port, host='::')
