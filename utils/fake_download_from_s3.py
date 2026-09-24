import shutil
def download_from_s3(localtarget, s3_uri):
    sys.stderr.write(f"FAKE MODE: Copying mock S3 at {{s3_uri}} to {{localtarget}}\n")
    sys.stderr.flush()
    s3_path = s3_uri.replace('s3://', '')
    task_dir = "{task_dir}"
    mock_path = os.path.join(task_dir, "s3_mock", s3_path)
    shutil.copy(mock_path, localtarget)
    return None
