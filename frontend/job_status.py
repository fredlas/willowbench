from flask import render_template, request, redirect, url_for, flash
import our_mongo as mdb
import forms
import datetime as dt
import time
import ast
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError, ClientError
from botocore.config import Config as Boto3Config
import requests
import json # Added for handling JSON response from _validate_and_submit_workflow

# NOTE: please set these to a real job ID and the owning user's email
EXAMPLE_JOB_ID = '20251012-212710-ZzRDaH-Optimus'
EXAMPLE_JOB_OWNER_EMAIL = 'fred.e.douglas1231@gmail.com'

import config
import workflows
from workflows import _validate_and_submit_workflow
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

def generate_s3_presigned_url(s3_uri, aws_region_name, expiration=86400):
    try:
      s3_client = boto3.client('s3', region_name=aws_region_name, config=Boto3Config(use_dualstack_endpoint = True))
    except (NoCredentialsError, PartialCredentialsError):
      log_error("failed to set up S3 client!")
      s3_client = None # Handle cases where credentials aren't available

    if s3_client is None or not s3_uri.startswith('s3://'):
        return s3_uri # Return original URI if S3 client not available or not an S3 URI

    try:
        # Parse bucket name and object key from s3://bucket-name/object/key
        parts = s3_uri[len('s3://'):].split('/', 1)
        if len(parts) < 2:
            return s3_uri # Invalid S3 URI format

        bucket_name = parts[0]
        object_key = parts[1]

        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_key},
            ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        log_error(f"S3 client error when getting a signed URL for {s3_uri}: error '{e}'")
        return s3_uri
    except Exception as e:
        log_error(f"when getting a signed URL for {s3_uri}: error '{e}'")
        return s3_uri


def get_status_color(status):
  if status.startswith('running'):
    return 'black'
  if status in ['finished', 'completed', 'done']:
    return 'green'
  if status in ['failed', 'cancelled']:
    return 'red'
  if status == 'pending':
    return 'gray'
  return 'black'

def format_exact_time(sse):
  as_dt = dt.datetime.fromtimestamp(sse)
  now_dt=dt.datetime.now()
  if as_dt.strftime('%Y') == now_dt.strftime('%Y'):
    return as_dt.strftime('(UTC) %H:%M:%S %A, %B %d, %Y')
  return as_dt.strftime('(UTC) %H:%M:%S, %Y-%m-%d')

def format_relative_time(sse):
  now = dt.datetime.now()
  past = dt.datetime.fromtimestamp(sse)
  diff = now - past

  seconds = diff.total_seconds()
  minutes = seconds / 60
  hours = minutes / 60
  days = hours / 24
  weeks = days / 7
  months = days / 30 # close enough
  years = days / 365

  minutes = int(minutes)
  hours = int(hours)
  days = int(days)
  weeks = int(weeks)
  months = int(months)
  years = int(years)

  if seconds < -1:
    return f"{int(seconds)} seconds in the future?!?!"
  if seconds < 2:
    return "just now"
  if seconds < 60:
    return f"{int(seconds)} seconds ago"
  if minutes < 60:
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
  if hours < 24:
    return f"{hours} hour{'s' if hours != 1 else ''} ago"
  if days < 7:
    return f"{days} day{'s' if days != 1 else ''} ago"
  if weeks < 4:
    return f"{weeks} week{'s' if weeks != 1 else ''} ago"
  if weeks < 52:
    return f"{months} month{'s' if months != 1 else ''} ago"
  return f"{years} year{'s' if years != 1 else ''} ago"

def jobs_status_page_handler(useremail):
  time_filter = request.args.get('time_filter', 'all')
  workflow_filter = request.args.get('workflow_filter', '').strip()
  status_filter = request.args.get('status_filter', 'all')
  scope_filter = request.args.get('scope_filter', 'user') # 'user' or 'customer'

  time_cutoff_sse = 0
  now_sse = time.time()
  if time_filter == 'hour':
      time_cutoff_sse = now_sse - 3600
  elif time_filter == 'day':
      time_cutoff_sse = now_sse - 3600 * 24
  elif time_filter == 'week':
      time_cutoff_sse = now_sse - 3600 * 24 * 7
  elif time_filter == 'month':
      time_cutoff_sse = now_sse - 3600 * 24 * 30 # Approximation
  elif time_filter == 'year':
      time_cutoff_sse = now_sse - 3600 * 24 * 365 # Approximation

  customer_id_to_query = None
  useremail_to_query = useremail

  if scope_filter == 'customer':
      customer_id_to_query = mdb.get_customer_id(useremail)
      if customer_id_to_query:
          useremail_to_query = None # Prioritize customer_id if available and scope is customer
      else:
          log_warn(f"Customer ID not found for user {useremail}, defaulting to user's jobs.")
          scope_filter = 'user' # Revert scope for template consistency


  jobs_cursor = mdb.find_jobs_for_user_with_filters(
      useremail=useremail_to_query,
      customer_id=customer_id_to_query,
      time_cutoff_sse=time_cutoff_sse if time_filter != 'all' else 0,
      workflow_substring=workflow_filter,
      status=status_filter
  )
  filtered_jobs = []
  for job in jobs_cursor:
    filtered_jobs.append({'job_id': job['job_id'],
                          'nickname': job.get('nickname'),
                          'useremail': job['owning_useremail'],
                          'status': job['status'],
                          'status_color': get_status_color(job['status']),
                          'last_activity_exact': format_exact_time(job.get('last_activity_sse', 0)),
                          'last_activity_relative': format_relative_time(job.get('last_activity_sse', 0)) })

  return render_template('jobs.html',
                         jobs=filtered_jobs,
                         time_filter=time_filter,
                         workflow_filter=workflow_filter,
                         status_filter=status_filter,
                         scope_filter=scope_filter)

def download_io_handler_public(inputs_or_outputs, key):
    return download_io_handler(EXAMPLE_JOB_OWNER_EMAIL, EXAMPLE_JOB_ID, inputs_or_outputs, key)

# inputs_or_outputs should be either 'inputs' or 'outputs'
def download_io_handler(useremail, job_id, inputs_or_outputs, key):
    job = mdb.get_job(useremail, job_id)
    if not job:
        return ("Job not found", 404)

    s3_uri = None
    if ':' in key:
        base_key, _, index_str = key.rpartition(':')
        if index_str.isdigit():
            index = int(index_str)
            array_val_str = job.get(inputs_or_outputs, {}).get(base_key)
            if isinstance(array_val_str, str):
                try:
                    file_list = ast.literal_eval(array_val_str)
                    if isinstance(file_list, list) and 0 <= index < len(file_list):
                        s3_uri = file_list[index]
                except (ValueError, SyntaxError):
                    log_error(f"Could not parse ArrayFile {inputs_or_outputs} '{base_key}'", job_id)

    if s3_uri is None:
        s3_uri = job.get(inputs_or_outputs, {}).get(key)

    if not s3_uri:
        log_error(f"{inputs_or_outputs} key '{key}' not found", job_id)
        return (f"{inputs_or_outputs} key '{key}' not found for job '{job_id}'", 404)

    signed_url = generate_s3_presigned_url(s3_uri, job.get('region', 'us-east-1'))
    return redirect(signed_url, code=302)

def view_io_handler_public(inputs_or_outputs, key):
    return view_io_handler(EXAMPLE_JOB_OWNER_EMAIL, EXAMPLE_JOB_ID, inputs_or_outputs, key)

# inputs_or_outputs should be either 'inputs' or 'outputs'
def view_io_handler(useremail, job_id, inputs_or_outputs, key):
    if not job_id or not key or inputs_or_outputs not in ['inputs', 'outputs']:
        return ("Invalid request parameters", 400)

    job = mdb.get_job(useremail, job_id)
    if not job:
        return ("Job not found", 404)

    s3_uri = None
    if ':' in key:
        base_key, _, index_str = key.rpartition(':')
        if index_str.isdigit():
            index = int(index_str)
            array_val_str = job.get(inputs_or_outputs, {}).get(base_key)
            if isinstance(array_val_str, str):
                try:
                    file_list = ast.literal_eval(array_val_str)
                    if isinstance(file_list, list) and 0 <= index < len(file_list):
                        s3_uri = file_list[index]
                except (ValueError, SyntaxError):
                    log_error(f"Could not parse ArrayFile {inputs_or_outputs} '{base_key}'", job_id)

    if s3_uri is None:
        s3_uri = job.get(inputs_or_outputs, {}).get(key)

    if not s3_uri:
        return (f"{inputs_or_outputs} key '{key}' not found for job '{job_id}'", 404)

    if not s3_uri.startswith('s3://'):
        return ("Invalid resource URI, expected s3 scheme", 400)
    fetch_url = generate_s3_presigned_url(s3_uri, job.get('region', 'us-east-1'))
    try:
        response = requests.get(fetch_url, timeout=30)
        response.raise_for_status()
        # Force nice inert text/plain for security TODO need to rethink if one of these is Markdown or nice HTML
        return response.text, 200, {'Content-Type': 'text/plain'}

    except requests.exceptions.RequestException as e:
        log_error(f"Error fetching {inputs_or_outputs} content from {fetch_url}: {e}", job_id)
        return (f"Error fetching {inputs_or_outputs} content: {e}", 500)
    except Exception as e:
        log_error(f"An unexpected error occurred while viewing {inputs_or_outputs} {key}: {e}", job_id)
        return (f"An unexpected error occurred: {e}", 500)


def update_job_comment_handler(useremail, job_id):
    form = forms.JustCSRFForm() # Instantiate the CSRF form, to be able to do validate_on_submit()
    if request.method == 'POST':
        if form.validate_on_submit(): # Validate the CSRF token
            try:
                # make it CSV-friendly, so we don't have to get too heavy-duty with parsing
                sanitized = request.form.get('comment').replace('\r', ' ').replace('\n', ' ').replace('"', "'")
                mdb.update_job_comment(useremail, job_id, sanitized)
            except Exception as e: # internal error
                log_error(f"update_job_comment_handler error: {e}", job_id)
        else: # CSRF validation failed
            pass
    else: # Method not allowed for non-POST requests
        pass
    return redirect(url_for('job_page_handler', job_id=job_id))

def process_resubmit_response(submit_response, old_job_id):
    if submit_response.status_code == 200:
        try:
            response_data = json.loads(submit_response.get_data(as_text=True))
            new_job_id = response_data.get('job_id')
            if new_job_id:
                flash(f'Job {old_job_id} successfully resubmitted as {new_job_id}.', 'success')
                return redirect(url_for('job_page_handler', job_id=new_job_id))
            else:
                flash('Resubmission successful, but new job ID not found for redirection.', 'warning')
        except json.JSONDecodeError:
            flash('Resubmission successful, but could not parse response from backend.', 'warning')
        return redirect(url_for('jobs_menu_handler')) # Fallback redirect in case of JSON parsing issues
    else:
        # If submission failed, extract the error message from the response body.
        error_message = submit_response.get_data(as_text=True)
        if not error_message:
            error_message = 'Unknown error during resubmission.'
        flash(f'Resubmission failed: {error_message}', 'danger')
        return redirect(url_for('job_page_handler', job_id=old_job_id))

def resubmit_job_handler(useremail, job_id):
    form = forms.JustCSRFForm() # Instantiate the CSRF form for validation
    if not form.validate_on_submit():
        flash('Invalid request for resubmission (CSRF token missing or invalid).', 'danger')
        return redirect(url_for('job_page_handler', job_id=job_id))

    job = mdb.get_job(useremail, job_id)
    if not job:
        flash('Original job not found.', 'danger')
        return redirect(url_for('jobs_menu_handler'))

    if job['status'] not in ['failed', 'cancelled']:
        flash('Job can only be resubmitted if its status is failed or cancelled.', 'warning')
        return redirect(url_for('job_page_handler', job_id=job_id))

    workflow_name = job['workflow_name']
    if workflow_name not in workflows.named_workflows:
        flash(f'Workflow "{workflow_name}" not found for resubmission.', 'danger')
        return redirect(url_for('job_page_handler', job_id=job_id))

    workflow_def = workflows.named_workflows[workflow_name]
    input_decls = workflow_def['inputs']

    submitted_data = {}
    for inpt_decl in input_decls:
        cur_name = inpt_decl['name']
        cur_type = inpt_decl['type']

        val = job['inputs'].get(cur_name)

        if val is None or val == '':
            submitted_data[cur_name] = 'false' if cur_type == 'Boolean' else ''
        elif isinstance(val, bool):
            submitted_data[cur_name] = str(val).lower()
        else:
            submitted_data[cur_name] = str(val)

    return process_resubmit_response(_validate_and_submit_workflow(input_decls, workflow_name,
                                                                   useremail, submitted_data),
                                     job_id)

def update_job_nickname_handler(useremail, job_id):
    form = forms.JustCSRFForm() # Instantiate the CSRF form, to be able to do validate_on_submit()
    if request.method == 'POST':
        if form.validate_on_submit(): # Validate the CSRF token
            try:
                # make it CSV-friendly, so we don't have to get too heavy-duty with parsing
                sanitized = request.form.get('nickname').replace('\r', ' ').replace('\n', ' ').replace('"', "'")
                mdb.update_job_nickname(useremail, job_id, sanitized)
            except Exception as e: # internal error
                log_error(f"update_job_nickname_handler error: {e}", job_id)
        else: # CSRF validation failed
            pass
    else: # Method not allowed for non-POST requests
        pass
    return redirect(url_for('job_page_handler', job_id=job_id))


def group_scattered_tasks(job):
  processed_task_logs = {}
  for wf_name, task_logs in job.get('task_logs', {}).items():
      grouped_tasks = {}
      for task_name, output_details in task_logs.items():
          if '_WLWSCTR' not in task_name:
              grouped_tasks[task_name] = {'type': 'single', 'task_name': task_name, 'output_details': output_details}
              continue

          parts = task_name.split('_WLWSCTR')
          base_name = parts[0]

          scatter_vars = []
          scatter_indices = []
          valid_scatter = True
          for part in parts[1:]:
              index_sep_ind = part.rfind('_')
              if index_sep_ind == -1: # No underscore found
                  valid_scatter = False
                  break
              var_name = part[:index_sep_ind]
              index_str = part[index_sep_ind+1:]
              if not var_name or not index_str.isdigit():
                  valid_scatter = False
                  break
              scatter_vars.append(var_name)
              scatter_indices.append(int(index_str))

          if not valid_scatter or not scatter_vars:
              # Treat as a single task if parsing fails
              grouped_tasks[task_name] = {'type': 'single', 'task_name': task_name, 'output_details': output_details}
              continue

          # Use sorted var names for a canonical group key
          group_key = f"{base_name}_scatter_{'_'.join(sorted(scatter_vars))}"

          if group_key not in grouped_tasks:
              # For display, use original var order
              display_vars = ', '.join(scatter_vars)
              grouped_tasks[group_key] = {
                  'type': 'scatter',
                  'group_key': group_key,
                  'display_name': f"{base_name} (scattered over {display_vars})",
                  'tasks': []
              }

          shard_display_parts = [f"{var}[{idx}]" for var, idx in zip(scatter_vars, scatter_indices)]
          shard_display_name = f"{base_name} for {', '.join(shard_display_parts)}"

          grouped_tasks[group_key]['tasks'].append({
              'task_name': shard_display_name,
              'output_details': output_details,
              'index': scatter_indices
          })

      # Sort tasks within each scatter group by index and create final list for template
      wf_log_list = []
      for key in sorted(grouped_tasks.keys()):
          group = grouped_tasks[key]
          if group['type'] == 'scatter':
              group['tasks'].sort(key=lambda t: t['index'])
          wf_log_list.append(group)
      processed_task_logs[wf_name] = wf_log_list
  return processed_task_logs

def _build_display_list(io_section_name, io_declarations, job_data, job_id):
    display_list = []
    for io_decl in io_declarations:
        name = io_decl['name']
        typ = io_decl['type']
        raw_val = job_data[io_section_name].get(name)

        processed_val = raw_val
        if typ == 'ArrayFile' and isinstance(raw_val, str):
            try:
                processed_val = ast.literal_eval(raw_val)
            except (ValueError, SyntaxError):
                log_error(f"Could not parse ArrayFile {io_section_name} '{name}' value: {raw_val}", job_id)
                processed_val = [] # Set to empty list on error

        display_list.append({'name': name, 'value': processed_val, 'type': typ})
    return display_list

def example_job_page_handler():
    return job_page_handler_logic(EXAMPLE_JOB_OWNER_EMAIL, EXAMPLE_JOB_ID, public_view=True)

def job_page_handler_logic(useremail, job_id, public_view=False):
  job = mdb.get_job(useremail, job_id)
  if not job:
    return (render_template('job_not_found.html', job_id=job_id, useremail=useremail), 404, {})

  workflow_name = job['workflow_name']
  wf = workflows.named_workflows.get(workflow_name)
  if not wf:
    log_error(f"workflow_name {workflow_name} not in workflows.named_workflows", job_id)
    flash('Workflow name not found.', 'error')
    return redirect(url_for('jobs_menu_handler'))

  return render_template('job_details.html', job=job, status_color=get_status_color(job['status']),
                         form=forms.JustCSRFForm(),
                         started_relative=format_relative_time(job.get('started_sse', 0)),
                         started_exact=format_exact_time(job.get('started_sse', 0)),
                         last_activity_exact=format_exact_time(job.get('last_activity_sse', 0)),
                         last_activity_relative=format_relative_time(job.get('last_activity_sse', 0)),
                         display_inputs=_build_display_list('inputs', wf['inputs'], job, job_id),
                         display_outputs=_build_display_list('outputs', wf['outputs'], job, job_id),
                         status_details=job.get('status_details', ''),
                         processed_task_logs=group_scattered_tasks(job),
                         public_view=public_view)

def cancel_to_core(job_id):
    port = config.get_backend_port()
    backend_url = f"http://[::1]:{port}"
    cancel_url = f"{backend_url}/cancel_workflow"
    # Switch to JSON request body
    response = requests.post(cancel_url, json={'job_id': job_id}, timeout=5)
    response.raise_for_status()

def cancel_job(useremail, job_id):
    # Verify user owns the job before cancellation
    job = mdb.get_job(useremail, job_id, allow_same_customer=False)
    if not job:
        flash('Job not found or not owned by you', 'danger')
        return redirect(url_for('jobs_menu_handler'))

    try:
        cancel_to_core(job_id)
        flash(f'Cancellation request sent for Job ID: {job_id}. Status will update shortly.', 'info')
    except requests.exceptions.RequestException as e:
        log_error(f'Error sending cancellation request: {e}', job_id)
        flash(f'Error sending cancellation request for Job ID {job_id}: {e}', 'danger')
    except Exception as e:
        log_error(f'An unexpected error occurred during cancellation: {e}', job_id)
        flash(f'An unexpected error occurred during cancellation for Job ID {job_id}: {e}', 'danger')

    return redirect(url_for('job_page_handler', job_id=job_id))
