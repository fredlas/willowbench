import json
import os
import subprocess
import requests
import random
import time
import sys
import datetime
import base64
import zlib
import concurrent.futures
import glob
import re
from urllib.parse import urlparse

import boto3
from botocore.config import Config as Boto3Config

# --- Parameters injected by ec2_launcher.py ---
frontend_base_url = "{frontend_base_url_val}"
task_secret = "{task_secret_val}" # pylint: disable=undefined-variable
aws_region = "{aws_region_val}"
docker_image_to_pull = "{docker_name_val}"
# --- End of injected parameters ---

# The VM should have AmazonS3FullAccess via its Instance Profile
s3 = boto3.client('s3', region_name=aws_region, config=Boto3Config(use_dualstack_endpoint = True))

def printflush(msg):
  print(msg)
  sys.stdout.flush()

def _ensure_willow_scratch_prefix(path):
    if not path.startswith('/willow_scratch/'):
        return '/willow_scratch/' + path
    return path

def _strip_willow_scratch_prefix(path):
    prefix = '/willow_scratch/'
    if path.startswith(prefix):
        return path[len(prefix):]
    return path

def cloudpath_from_localpath(_s3_bucket, _job_id, _task_name, vm_filepath):
    sanitized_fpath = re.sub(r'[^a-zA-Z0-9_.()/-]+', '-', _strip_willow_scratch_prefix(vm_filepath))
    return f's3://{{_s3_bucket}}/{{_job_id}}/{{_task_name}}/{{sanitized_fpath}}'

def download_from_s3(localtarget, s3_uri):
    try:
        printflush(f"Downloading {{s3_uri}} to {{localtarget}}")
        bucket, key = s3_uri.replace('s3://', '').split('/', 1)
        s3.download_file(bucket, key, localtarget)
        printflush(f"Successfully downloaded {{s3_uri}} to {{localtarget}}")
        return None
    except Exception as e:
        err_msg = f'localization of {{s3_uri}} to {{localtarget}} failed: {{e}}'
        printflush(f"ERROR: {{err_msg}}")
        return err_msg
# END download_from_s3

def download_from_http(localtarget, source_uri):
    try:
        printflush(f"Downloading {{source_uri}} to {{localtarget}}")
        with requests.get(source_uri, stream=True, timeout=10) as r:
            r.raise_for_status()
            with open(localtarget, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        printflush(f"Successfully downloaded {{source_uri}} to {{localtarget}}")
        return None
    except Exception as e:
        err_msg = f'localization of {{source_uri}} to {{localtarget}} failed: {{e}}'
        printflush(f"ERROR: {{err_msg}}")
        return err_msg

def localize_all_files(files_to_localize):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for _, cloudpath in files_to_localize.items():
            if cloudpath.startswith('s3://'):
                _, key = cloudpath.replace('s3://', '').split('/', 1)
                localtarget = _ensure_willow_scratch_prefix(key)
                os.makedirs(os.path.dirname(localtarget), exist_ok=True)
                futures.append(executor.submit(download_from_s3, localtarget, cloudpath))
            elif cloudpath.startswith('http://') or cloudpath.startswith('https://'):
                path = urlparse(cloudpath).path.lstrip('/')
                localtarget = _ensure_willow_scratch_prefix(path)
                os.makedirs(os.path.dirname(localtarget), exist_ok=True)
                futures.append(executor.submit(download_from_http, localtarget, cloudpath))
            else:
                return f"unknown scheme:// for {{cloudpath}}"

        for future in concurrent.futures.as_completed(futures):
            err_str = future.result()
            if err_str:
                return err_str # Return on first error
    return None

def upload_to_s3(local_path, s3_uri):
    try:
        printflush(f"Uploading {{local_path}} to {{s3_uri}}")
        if not os.path.exists(local_path):
             raise FileNotFoundError(f"Local file {{local_path}} not found for upload.")
        bucket, key = s3_uri.replace('s3://', '').split('/', 1)
        s3.upload_file(local_path, bucket, key)
        return None
    except Exception as e:
        err_msg = f'cloudization of {{local_path}} to {{s3_uri}} failed: {{e}}'
        printflush(f"ERROR: {{err_msg}}")
        return err_msg
# END upload_to_s3

def cloudize_all_files(output_files, optional_cloudizes, glob_files, s3_bucket, job_id, task_name):
    # output_files is fqvn -> local_path
    # glob_files is local_path -> cloud_path

    # Prepare all uploads (this maps local_path -> cloud_path)
    all_uploads = {{}} # pylint: disable=unhashable-member
    local_to_fqvn = {{}} # pylint: disable=unhashable-member

    skipped_optionals = []
    # Process WDL output files
    for fqvn, local_path in output_files.items():
        if fqvn in optional_cloudizes and not os.path.exists(local_path):
            printflush(f"WARNING: Optional output file '{{fqvn}}' at '{{local_path}}' not found. Skipping upload.")
            skipped_optionals.append(fqvn)
            continue
        cloud_path = cloudpath_from_localpath(s3_bucket, job_id, task_name, local_path)
        all_uploads[local_path] = cloud_path # pylint: disable=unsupported-assignment-operation
        local_to_fqvn[local_path] = fqvn # pylint: disable=unsupported-assignment-operation

    # Add glob files, avoiding duplicates
    for local_path, cloud_path in glob_files.items():
        if local_path not in all_uploads:
            all_uploads[local_path] = cloud_path # pylint: disable=unsupported-assignment-operation

    printflush(f'all_uploads is {{all_uploads}}')

    # Execute uploads in parallel
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_local_path = {{
            executor.submit(upload_to_s3, local_src, cloudpath_dst): local_src
            for local_src, cloudpath_dst in all_uploads.items() # pylint: disable=no-member
        }}

        cloudized_fqvn_map = {{}} # pylint: disable=unhashable-member
        errors = []
        for future in concurrent.futures.as_completed(future_to_local_path):
            local_path = future_to_local_path[future] # pylint: disable=unsubscriptable-object
            err_str = future.result()
            if err_str:
                errors.append(err_str)
            elif local_path in local_to_fqvn:
                # This was a WDL output file, so we need to report its FQVN and cloud path
                fqvn = local_to_fqvn[local_path] # pylint: disable=unsubscriptable-object
                cloud_path = all_uploads[local_path] # pylint: disable=unsubscriptable-object
                cloudized_fqvn_map[fqvn] = cloud_path # pylint: disable=unsupported-assignment-operation

        if errors:
            return "\n".join(errors), cloudized_fqvn_map

    for skipped in skipped_optionals:
        cloudized_fqvn_map[skipped] = '' # pylint: disable=unsupported-assignment-operation
    return None, cloudized_fqvn_map


def create_and_run_docker_command(commands):
    if isinstance(commands, list):
        script_content = "\n".join(commands)
    elif isinstance(commands, str):
        script_content = commands
    else:
        return 1, "", f"Error: Unsupported type for 'commands': {{type(commands)}}"

    try:
        time_before_docker = datetime.datetime.utcnow().isoformat() + "Z"
        ret_stderr = f"{{time_before_docker}} - Willowbench: Attempting to pull Docker image {{docker_image_to_pull}}\n"
        pull_command = ["docker", "pull", docker_image_to_pull]
        pull_result = subprocess.run(pull_command, capture_output=True, text=True, check=False)
        time_after_docker = datetime.datetime.utcnow().isoformat() + "Z"
        if pull_result.returncode != 0:
            err_msg = f"Failed to pull Docker image '{{docker_image_to_pull}}'. Stderr: {{pull_result.stderr}}"
            ret_stderr += f"{{time_after_docker}} - Willowbench: ERROR: '{{err_msg}}'\n"
            return 1, "", ret_stderr
        ret_stderr += f"{{time_after_docker}} - Willowbench: Docker image '{{docker_image_to_pull}}' pulled successfully.\n"

        host_script_path = os.path.join("/willow_system", "run_bundled_willow_task_commands.sh")
        with open(host_script_path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write("\n cd /willow_docker_home\n")
            f.write(script_content)
            f.write("\n sync") # ensure files are available before moving on to cloudization
        os.chmod(host_script_path, 0o755)

        shell_bootstrap_script = f'if [ -x /bin/bash ]; then exec /bin/bash {{host_script_path}}; else exec /bin/sh {{host_script_path}}; fi'
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"/willow_scratch:/willow_docker_home",
            "-v",
            "/willow_system:/willow_system",
            docker_image_to_pull,
            "/bin/sh",
            "-c",
            shell_bootstrap_script
        ]

        time_before_cmd = datetime.datetime.utcnow().isoformat() + "Z"
        ret_stderr += f"{{time_before_cmd}} - Willowbench: Starting Docker container command(s) execution.\n"
        result = subprocess.run(docker_command, capture_output=True, text=True, check=False)
        ret_stderr += result.stderr + '\n'
        time_after_cmd = datetime.datetime.utcnow().isoformat() + "Z"
        ret_stderr += f"{{time_after_cmd}} - Willowbench: Dockerized command(s) execution finished.\n"
        return result.returncode, result.stdout, ret_stderr

    except Exception as e:
        err_msg = f'Python exception while running Docker command: {{e}}'
        printflush(f"ERROR: {{err_msg}}")
        return 1, '', err_msg


def run_dockerless(bash_cmds):
    try:
        if isinstance(bash_cmds, list):
            script_content = "\n".join(bash_cmds)
        elif isinstance(bash_cmds, str):
            script_content = bash_cmds
        else:
            return 1, "", f"Error: Unsupported type for 'commands': {{type(commands)}}"

        host_script_path = os.path.join("/willow_system", "run_bundled_willow_task_commands.sh")
        with open(host_script_path, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("\ncd /willow_scratch\n")
            f.write(script_content)
            f.write("\n sync") # ensure files are available before moving on to cloudization
        os.chmod(host_script_path, 0o755)

        result = subprocess.run(
            ["/bin/bash", host_script_path],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode, result.stdout, result.stderr

    except Exception as e:
        err_msg = f'Python exception while running commands: {{e}}'
        printflush(f"ERROR: {{err_msg}}")
        return 1, '', err_msg


def report_with_backoff(method, payload):  # pylint: disable=unused-argument
    base_delay = 2.0
    url = f'{{frontend_base_url}}{{method}}'
    for attempt in range(8):
        try:
            printflush(f"doing a {{frontend_base_url}}{{url}} report with payload {{payload}}")
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            printflush(f"Reported to {{method}} successfully.")
            return
        except requests.exceptions.RequestException as e:
            delay = random.uniform(base_delay, 2.0 * base_delay)
            printflush(f'Error reporting to {{method}}: {{e}}. Retrying in {{delay:.2f}}s (attempt {{attempt + 1}}/8)')
            time.sleep(delay)
        base_delay = min(base_delay * 2, 60)
    printflush(f"ERROR: Failed to report to {{method}} after multiple retries.")

def read_requested_files(file_contents_requests):
    file_contents = {{}} # pylint: disable=unhashable-member
    for fname in file_contents_requests:
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                file_contents[fname] = f.read() # pylint: disable=unsupported-assignment-operation
        except FileNotFoundError:
            return None, f'Could not find file {{fname}}'
        except IOError:
            return None, f'Error reading file {{fname}}'
    return file_contents, None

def report_status(job_id, task_name, status='OK', error_details=None, stdout='', stderr='', cloudpaths=None,
                  stdout_requested=False, file_contents_requests=None, glob_result_lists=None):
    payload = {{  # pylint: disable=unhashable-member
        'job_id': job_id,
        'task_name': task_name,
        'secret': task_secret, # Use the injected secret
        'stdout': stdout,
        'stderr': stderr,
        'cloudpaths': cloudpaths if cloudpaths else {{}}, # pylint: disable=unhashable-member
        'include_cmd_stdout': stdout_requested
    }}
    if error_details:
        payload['error_details'] = error_details # pylint: disable=unsupported-assignment-operation

    file_contents = {{}} # pylint: disable=unhashable-member
    if status == 'OK':
        file_contents, err_str = read_requested_files(file_contents_requests)
        if err_str:
            status = 'Reading file contents failed'
            if not payload.get('error_details'): # pylint: disable=no-member
                payload['error_details'] = err_str # pylint: disable=unsupported-assignment-operation
    if file_contents:
        payload['file_contents'] = file_contents # pylint: disable=unsupported-assignment-operation

    if glob_result_lists:
        payload['glob_result_lists'] = glob_result_lists # pylint: disable=unsupported-assignment-operation

    payload['status'] = status # pylint: disable=unsupported-assignment-operation
    report_with_backoff('/task/task_done', payload)

def gather_globs(glob_requests, s3_bucket, job_id, task_name):
    # maps from glob pattern to list of cloud paths
    glob_result_lists = {{}} # pylint: disable=unhashable-member
    # maps from local path to cloud path
    glob_files_to_upload = {{}} # pylint: disable=unhashable-member

    for glob_pattern in glob_requests:
        local_paths = glob.glob(glob_pattern)
        cloud_paths = []
        for local_path in local_paths:
            cloud_path = cloudpath_from_localpath(s3_bucket, job_id, task_name, local_path)
            glob_files_to_upload[local_path] = cloud_path # pylint: disable=unsupported-assignment-operation
            cloud_paths.append(cloud_path)
        glob_result_lists[glob_pattern] = cloud_paths # pylint: disable=unsupported-assignment-operation

    return glob_result_lists, glob_files_to_upload

if __name__ == '__main__':
    exit_code = 1
    stdout = ""
    stderr = ""
    final_status = "Initialization error"
    error_details = None
    cloudized_paths = {{}} # pylint: disable=unhashable-member
    file_contents_requests = []
    stdout_requested = False
    glob_result_lists = {{}} # pylint: disable=unhashable-member

    try:
        # Parse config early to get job/task names for reporting even if other steps fail
        printflush("now parsing config JSON")
        with open("/willow_system/willow_task_config.json", 'r') as f:
            config = json.load(f)
        job_id = config.get('job_id', 'undefined_job')
        task_name = config.get('task_name', 'undefined_task')

        # Ensure all local paths start with /willow_scratch/ as soon as they are parsed
        files_to_localize = {{_ensure_willow_scratch_prefix(k): v for k, v in config.get('files_to_localize', {{}}).items()}} # pylint: disable=unhashable-member
        files_to_cloudize = {{k: _ensure_willow_scratch_prefix(v) for k, v in config.get('files_to_cloudize', {{}}).items()}} # pylint: disable=unhashable-member
        optional_cloudizes = set(config.get('optional_cloudizes', [])) # pylint: disable=unhashable-member
        bash_cmds_encoded = config.get('bash_cmds')
        s3_bucket = config.get('s3_bucket', None)
        if not s3_bucket:
            raise Exception('empty or missing s3_bucket item of config JSON')
        glob_requests = config.get('glob_requests', [])
        file_contents_requests = config.get('file_contents_requests', [])
        stdout_requested = config.get('stdout_requested', False)
        if not bash_cmds_encoded:
            raise Exception("empty bash_cmds")
        bash_cmds_compressed = base64.b64decode(bash_cmds_encoded)
        bash_cmds = zlib.decompress(bash_cmds_compressed).decode('utf-8')

        printflush(f"Starting task: {{job_id}} / {{task_name}}")

        os.chdir('/willow_scratch')
        printflush("now localizing all files")
        localize_err = localize_all_files(files_to_localize)
        printflush("done localizing all files")
        if localize_err:
            printflush(f"localization error {{localize_err}}")
            final_status = "Localization failed"
            error_details = localize_err
            stderr = final_status
            raise Exception(final_status)

        if docker_image_to_pull != 'none':
            returncode, stdout, stderr = create_and_run_docker_command(bash_cmds)
        else:
            returncode, stdout, stderr = run_dockerless(bash_cmds)

        if returncode != 0:
            final_status = f'Task execution failed with code {{returncode}}'
            error_details = f"stderr:\n{{stderr}}\n\nstdout:\n{{stdout}}"
            exit_code = returncode
            raise Exception(final_status)

        # gather_globs finds glob matches, and prepares them for upload.
        # It returns glob results for the task_done message, and a map of local->cloud paths for uploading.
        glob_result_lists, glob_files_to_upload = gather_globs(glob_requests, s3_bucket, job_id, task_name)

        # files_to_cloudize is from config, and is fqvn -> local_path.
        # cloudize_all_files will upload both WDL output files and globbed files.
        # It returns a map of fqvn->cloudpath for WDL outputs.
        cloudize_err, cloudized_paths = cloudize_all_files(files_to_cloudize, optional_cloudizes,
                                                           glob_files_to_upload, s3_bucket, job_id, task_name)
        if cloudize_err:
            printflush(f"cloudize error {{cloudize_err}}")
            final_status = "Cloudization failed"
            error_details = cloudize_err
            stderr = final_status
            raise Exception(final_status)

        final_status = 'OK'
        exit_code = 0 # Success

    except json.JSONDecodeError as e:
        final_status = 'Invalid task config json'
        error_details = str(e)
        stderr = final_status
        printflush(f"ERROR: {{final_status}}")
    except Exception as e:
        # If final_status wasn't set by a specific step, use generic error
        if final_status == "Initialization error":
             final_status = f'outpost.py failed'
             # Add traceback if it was a generic exception
             import traceback # pylint: disable=unused-import
             error_details = f"{{e}}\n{{traceback.format_exc()}}"
             stderr = error_details if stderr == "" else stderr
        elif not error_details:
             error_details = str(e)
        printflush(f"ERROR: {{final_status}}")
        # exit_code remains 1 (default failure) or the command's return code

    finally:
        # Always attempt to report the final status
        printflush(f"Reporting final status: {{final_status}}")
        report_status(job_id, task_name, status=final_status, error_details=error_details, stdout=stdout, stderr=stderr,
                      cloudpaths=cloudized_paths, stdout_requested=stdout_requested,
                      file_contents_requests=file_contents_requests,
                      glob_result_lists=glob_result_lists)
        printflush(f"outpost.py exiting with code {{exit_code}}")
        sys.exit(exit_code) # Ensure script exits with the determined code
