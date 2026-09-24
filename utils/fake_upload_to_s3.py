def upload_to_s3(local_path, s3_uri):
    sys.stderr.write(f"FAKE MODE: Copying {{local_path}} to mock S3 at {{s3_uri}}\n")
    sys.stderr.flush()
    s3_path = s3_uri.replace('s3://', '')
    task_dir = "{task_dir}"
    mock_path = os.path.join(task_dir, "s3_mock", s3_path)
    os.makedirs(os.path.dirname(mock_path), exist_ok=True)
    time.sleep(1)
    shutil.copy(local_path, mock_path)
    return None
