#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import shutil

# NOTE: You provide input files in "fake S3" by taking task_dir below, and if you want e.g.
#       s3://willowtest/whoa, you write it to f"{task_dir}/s3_mock/willowtest/whoa"

def crash(msg):
    print(msg)
    sys.exit(1)

def replace_function_in_script(script_content, function_name, new_function_body):
    pattern = rf"(def\s+{re.escape(function_name)}\s*\(.*?\):.*?)^# END {re.escape(function_name)}"
    match = re.search(pattern, script_content, re.DOTALL | re.MULTILINE)
    if match:
        start_of_function_def = script_content.find(match.group(0))
        end_of_function_def = start_of_function_def + len(match.group(0))
        modified_script_content = (
            script_content[:start_of_function_def] +
            new_function_body.strip() +  # .strip() to remove leading/trailing whitespace
            "\n# END " + function_name + "\n" + # Re-add the END marker with a newline for consistency
            script_content[end_of_function_def:]
        )
        return modified_script_content
    else:
        print(f"Warning: Function '{function_name}' not found or '# END {function_name}' marker missing.")
        return script_content

def main():
    parser = argparse.ArgumentParser(description="Fake EC2 launcher for local testing.")
    parser.add_argument("--task-secret", required=True, help="Secret token for the task.")
    parser.add_argument("--task-config-json", required=True, help="JSON string containing task configuration.")
    parser.add_argument("--frontend-base-url", required=True, help="Base URL for the task to report status.")
    parser.add_argument("--aws-region", required=True, help="AWS region (ignored for local testing).")
    parser.add_argument("--docker-name", required=True, help="Name of Docker image to run in, 'none' for no Docker.")
    parser.add_argument("--dev", action="store_true", help="Run in development mode.")
    parser.add_argument("--prod", action="store_true", help="Run in production mode.")
    parser.add_argument("--use-willows-own-vpc", action="store_true", help="The task will be run in Willow's own AWS account.")
    parser.add_argument("--sec-grp", help="AWS security group to launch the instance in.")
    parser.add_argument("--subnet", help="AWS subnet to launch the instance in.")
    parser.add_argument("--customer-aws-id", help="Customer's AWS account ID.")
    parser.add_argument("--ami-id", required=True, help="AWS AMI the VM should be created from.")
    parser.add_argument("--instance-type", required=True, help="AWS VM type to use, e.g. t3.micro.")

    args = parser.parse_args()

    sys.stderr.write("fake_ec2_launcher.py running!\n")
    sys.stderr.flush()

    job_id_random = json.loads(args.task_config_json)['job_id'][16:22]
    task_dir = f'/tmp/willow_fakes/{job_id_random}'
    os.makedirs(task_dir, exist_ok=True)

    try:
        # Format outpost.py: first fill in the normal templated vars...
        with open(os.path.join(os.path.dirname(__file__), "outpost_template.py"), 'r') as f:
            template = f.read()
        final_script = template.format(frontend_base_url_val=args.frontend_base_url,
                                       task_secret_val=args.task_secret,
                                       aws_region_val=args.aws_region,
                                       docker_name_val=args.docker_name)
        # ...then, get a little crazy and replace some functions.
        with open(os.path.join(os.path.dirname(__file__), "fake_upload_to_s3.py"), 'r') as f:
            new_upload_to_s3_function_template = f.read()
        new_upload_to_s3_function = new_upload_to_s3_function_template.format(task_dir=task_dir)
        final_script = replace_function_in_script(final_script, "upload_to_s3", new_upload_to_s3_function)

        with open(os.path.join(os.path.dirname(__file__), "fake_download_from_s3.py"), 'r') as f:
            new_download_from_s3_function_template = f.read()
        new_download_from_s3_function = new_download_from_s3_function_template.format(task_dir=task_dir)
        final_script = replace_function_in_script(final_script, "download_from_s3", new_download_from_s3_function)

        # Write task config
        config_path = os.path.join(task_dir, "willow_task_config.json")
        final_script = final_script.replace('/tmp/willow_task_config.json', config_path)
        with open(config_path, "w") as f:
            f.write(args.task_config_json)

        outpost_path = os.path.join(task_dir, "outpost.py")
        sys.stderr.write("path is: "+outpost_path+"\n")
        sys.stderr.flush()
        with open(outpost_path, "w") as f:
            f.write(final_script)

        # Run the task in background
        subprocess.Popen(
            ["/usr/bin/python3", outpost_path],
            cwd=task_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True
        )
        # Print fake instance ID
        print(f"local-{job_id_random}")

    except Exception as e:
        crash(f"Error running local task: {e}")

if __name__ == "__main__":
    main()
