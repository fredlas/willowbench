import os
import time
import random
import string
import datetime
import json
import requests
import pymongo
import shutil
import subprocess
import datetime as dt
import sys

def log_test(the_msg, job_id):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_part = '' if job_id == 'none' else f' [job_id={job_id}]'
  sys.stderr.write(f"{current_time} [wdlr][T] {the_msg}{job_part}\n")
  sys.stderr.flush()

# Verify we're in dev environment
try:
    with open("/home/admin/note_this_machine_is_dev", "r") as f:
        content = f.read().strip()
        if content != "just in case you are doubting your sanity":
            print("Not in dev environment - aborting test")
            exit(1)
except FileNotFoundError:
    print("Dev marker file not found - aborting test")
    exit(1)

def generate_job_id(workflow_name):
    now_utc = datetime.datetime.utcnow()
    date_str = now_utc.strftime("%Y%m%d")
    time_str = now_utc.strftime("%H%M%S")
    allowed_chars = string.ascii_letters + string.digits + "_"
    random_chars = ''.join(random.choices(allowed_chars, k=6))
    cleaned_workflow_name = ''.join(c if c in allowed_chars else 'X' for c in workflow_name)
    return f"{date_str}-{time_str}-{random_chars}-{cleaned_workflow_name}"

def prepare_fake_s3_file(task_dir, s3_path, content):
    file_path = os.path.join(task_dir, 's3_mock', s3_path)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        f.write(content)

# Get core port from file
try:
    with open("/home/admin/willow/frontend/willow_backend_port.txt", "r") as f:
        core_port = f.read().strip()
except FileNotFoundError:
    print("Core port file not found")
    exit(1)

job_id = generate_job_id("FakeEndToEndTest")
log_test('generated job_id', job_id)

# Real input files on fake AWS S3
job_id_random = job_id[16:22]
task_dir = f'/tmp/willow_fakes/{job_id_random}'

# the_input_file
prepare_fake_s3_file(task_dir, "willowtestuploads/whoa", "haha yay HOORAY\n and yay again 1 2 3 wheeeee")
# two_input_files
prepare_fake_s3_file(task_dir, "willowtestuploads/file0", "first file contents")
prepare_fake_s3_file(task_dir, "willowtestuploads/file1", "contents of other file")

val_of_struct_named_foo = {"a": "6", "b": "1", "is": {"c": "770"}}
json_of_struct_named_foo = json.dumps(val_of_struct_named_foo)
payload = {
    "job_id": job_id,
    "workflow_name": "FakeEndToEndTest",
    "customer_id": 101030,
    "owning_useremail": "a@a.a",
    "inputs": {
        "the_input_file": "s3://willowtestuploads/whoa",
        "set_me_false": "False",
        "set_me_true": "True",
        "set_me_3": "3",
        "foo": json_of_struct_named_foo,
        "two_input_files": "[\"s3://willowtestuploads/file0\", \"s3://willowtestuploads/file1\"]",
        "two_input_ints": "[1096001096, 11133]"
    },
    "input_types": {
        "the_input_file": "File",
        "set_me_false": "Boolean",
        "set_me_true": "Boolean",
        "set_me_3": "Int",
        "foo": "HeresMyStruct",
        "two_input_files": "ArrayFile",
        "two_input_ints": "ArrayInt"
    }
}

url = f"http://[::1]:{core_port}/run_willow_workflow"
try:
    log_test('about to submit test job', job_id)
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    log_test('Successfully submitted test job', job_id)
except Exception as e:
    log_test('error submitting test job', job_id)
    exit(1)

# Connect to MongoDB
try:
    client = pymongo.MongoClient("mongodb://127.0.0.1:27017")
    mongo_willow_db = client['willow_database']
    jobs_collection = mongo_willow_db['jobs']
except Exception as e:
    log_test('MongoDB connection failed', job_id)
    exit(1)

# Wait for job completion with timeout
for i in range(30):
    job = jobs_collection.find_one({"job_id": job_id})
    if job:
        status = job.get("status", "")
        if not status.startswith(("pending", "running")):
            break
    time.sleep(4)

def verify_result(job_res, varname, expected_val):
    actual_val = job_res['outputs'][f'{varname}']
    if actual_val != expected_val:
        log_test(f'wrong {varname}: {actual_val}, expected {expected_val}', job_id)
        return False
    return True

def check_results():
    all_correct = True
    try:
        if status != "completed":
            log_test(f'FAIL: fake e2e test final status is {status} (expected "completed")', job_id)
            all_correct = False
        job_res = jobs_collection.find_one({'job_id': job_id})
        all_correct &= verify_result(job_res, 'the_incremented_sum', '40')
        all_correct &= verify_result(job_res, 'the_read_int_stdout', '123987')
        all_correct &= verify_result(job_res, 'the_read_float_file', '3.14159')
        all_correct &= verify_result(job_res, 'imported_gather_out', '6')
        all_correct &= verify_result(job_res, 'imp_conditional_out_true', '333')
        all_correct &= verify_result(job_res, 'imp_conditional_out_false', '999')
        all_correct &= verify_result(job_res, 'struct_sum_out', '777')
        all_correct &= verify_result(job_res, 'ungathered', '6')
        all_correct &= verify_result(job_res, 'oolong', 'first file contents1096001096\n')
        all_correct &= verify_result(job_res, 'lily', 'contents of other file11133\n')
        all_correct &= verify_result(job_res, 'read_tsv_success', 'True')

        task_logs = job_res['task_logs']
        FakeEndToEndTest_logs = task_logs['FakeEndToEndTest']
        for i in range(4):
            if f'capitalize_expect_NOT_run{i}' in FakeEndToEndTest_logs:
                log_test(f'FAIL: looks like capitalize_expect_NOT_run{i}, DID run: present in {FakeEndToEndTest_logs.keys()}', job_id)
                all_correct = False
        for i in range(2):
            if f'capitalize_expect_to_run{i}' not in FakeEndToEndTest_logs:
                log_test(f'FAIL: looks like capitalize_expect_to_run{i} didnt run: not present in {FakeEndToEndTest_logs.keys()}', job_id)
                all_correct = False
        if 'scttrNOT_AT_ALL' in FakeEndToEndTest_logs:
            log_test(f'FAIL: looks like scttrNOT_AT_ALL, DID run: present in {FakeEndToEndTest_logs.keys()}', job_id)
            all_correct = False
    except Exception as e:
        log_test(f'FAKE END-TO-END TEST FAILED WITH EXCEPTION: {e}', job_id)
        return False
    return all_correct

if check_results():
    print("\nFake end-to-end test successful!")
    jobs_collection.delete_one({"job_id": job_id})
    shutil.rmtree(task_dir, ignore_errors=True)
    exit(0)
else:
    log_test(f"FAKE END-TO-END TEST FAILED. Final status: {status}", job_id)
    print("\nJob details from MongoDB:")
    print(json.dumps(job, indent=2, default=str))
    exit(1)
