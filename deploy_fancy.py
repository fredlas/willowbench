#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import time
import requests

NUM_WIDDLERS = 2

# Exclude patterns for rsync operations
EXCLUDE_PATTERNS = ['.aider*', '__pycache__', 'zzz_notes_and_stuff', '.git', 'target', 'Cargo.lock',
                    '.gitignore', 'core/herder_cutoff_map.txt', 'frontend/willow_backend_port.txt']

# Component definitions with their dependencies
COMPONENTS = {
    "frontend": {
        "paths": ["frontend", "accounts/willowbench_format_new_customer_aws_vpc.py", "workflows"],
        "deploy_cmd": "cd /home/admin/willow/frontend && ./deploy.sh"
    },
    "fe_lint": {
        "paths": ["frontend"],
        "local_lint_cmd": "cd frontend && pylint --load-plugins pylint_flask --disable=W0311,C0301,C0114,C0116,W0718,C0103,C0413,C0411,C0115,R0911,W0603,W1514,W0621,R0914,R0903,R0913,R1735,R1705,W0511,R0917 *.py"
    },
    "core": {
        "paths": ["core", "utils"],
        "deploy_cmd": "cd /home/admin/willow/core && ./deploy.sh"
    },
    "core_lint": {
        "paths": ["utils"],
        "local_lint_cmd": "cd utils && ./lint_ec2_launcher.sh"
    },
    "the_widdler": {
        "paths": ["the_widdler", "workflows"],
        "deploy_cmd": "cd /home/admin/willow/the_widdler && ./deploy.sh " + str(NUM_WIDDLERS)
    },
    "widdler_lint": {
        "paths": ["the_widdler"],
        "local_lint_cmd": "cd the_widdler && ./lint_the_widdler.sh"
    }
}

def run_ssh_command(remote, command):
    subprocess.run(["ssh", "-i", os.path.expanduser("~/.ssh/adminfredtest.pem"), remote, command],
                   check=True)

def dry_run_rsync(remote_target):
    rsync_cmd = ["rsync", "-avz", "--dry-run", "-e", "ssh -i ~/.ssh/adminfredtest.pem",
                "--exclude=.aider*", "--exclude=__pycache__", "--exclude=zzz_notes_and_stuff",
                "--exclude=.git", "--exclude=target", "--exclude=Cargo.lock", "--exclude=.gitignore",
                "--exclude=core/herder_cutoff_map.txt", "--exclude=frontend/willow_backend_port.txt",
                "--out-format=%n", ".", f"{remote_target}:willow/"]
    print("Running dry-run rsync...")
    scp_result = subprocess.run(rsync_cmd, capture_output=True, text=True)

    if scp_result.returncode != 0:
        print("Dry-run failed!")
        print(scp_result.stderr)
        raise Exception("Dry-run failed")

    # Get list of changed files from rsync output
    files = [line.strip() for line in scp_result.stdout.splitlines() if line.strip()]
    def keep_it(the_str):
        strings_to_remove = ["sending incremental file list", "./", "core/", "frontend/", "the_widdler/"]
        if the_str in strings_to_remove:
            return False
        if "received" in the_str and "bytes/sec" in the_str:
            return False
        if "total size is" in the_str:
            return False
        return True
    files = [file for file in files if keep_it(file)]
    return files

def run_rsync(remote_target):
    rsync_cmd = ["rsync", "-avz", "-e", "ssh -i ~/.ssh/adminfredtest.pem",
                "--exclude=.aider*", "--exclude=__pycache__", "--exclude=zzz_notes_and_stuff",
                "--exclude=.git", "--exclude=target", "--exclude=Cargo.lock", "--exclude=.gitignore",
                "--exclude=core/herder_cutoff_map.txt", "--exclude=frontend/willow_backend_port.txt",
                "--out-format=%n", ".", f"{remote_target}:willow/"]
    print("Running rsync...")
    scp_result = subprocess.run(rsync_cmd, capture_output=True, text=True)

    if scp_result.returncode != 0:
        print("File copy failed!")
        print(scp_result.stderr)
        raise Exception("File copy failed")

    # Get list of changed files from rsync output
    files = [line.strip() for line in scp_result.stdout.splitlines() if line.strip()]
    def keep_it(the_str):
        strings_to_remove = ["sending incremental file list", "./", "core/", "frontend/", "the_widdler/"]
        if the_str in strings_to_remove:
            return False
        if "received" in the_str and "bytes/sec" in the_str:
            return False
        if "total size is" in the_str:
            return False
        return True
    files = [file for file in files if keep_it(file)]
    return files

SKIP_E2E = 0
FORCE_E2E = 1
NORMAL_E2E = 2
IGNORE_E2E = 3

def deploy(remote_target, args, is_dev, e2e_forcing, to_deploy):
    changed_files = run_rsync(remote_target)
    print(f"changed_files:\n {changed_files}\n\n")

    # Execute build commands if needed
    build_and_lint = 'true'
    if to_deploy['core']:
        build_and_lint += " && cd /home/admin/willow/core && /home/admin/.cargo/bin/cargo build --release"
    if to_deploy['the_widdler']:
        build_and_lint += " && cd /home/admin/willow/the_widdler && /home/admin/.cargo/bin/cargo build --release"
    if len(build_and_lint) > 4:
        print("FANCY DEPLOY: Running builds on remote...")
        proc = subprocess.run(["ssh", "-i", os.path.expanduser("~/.ssh/adminfredtest.pem"), args.remote,
                                build_and_lint], capture_output=True, encoding='utf-8')
        if proc.returncode != 0:
            raise Exception(f'Build failed:{proc.stdout}\n\n===============\n\n{proc.stderr}\n')
        print("FANCY DEPLOY: ...all built.")

    if to_deploy["the_widdler"]:
        to_deploy["core"] = False # widdler deploy handles core deploy as a subroutine

    for comp in ["the_widdler", "core", "frontend"]:
        if to_deploy[comp]:
            print("=========================================================================")
            print(f"FANCY DEPLOY: Deploying {comp}...")
            cmd = COMPONENTS[comp]["deploy_cmd"]
            try:
                run_ssh_command(args.remote, cmd)
            except subprocess.CalledProcessError as e:
                print(f"Deployment of {comp} failed: {e}")
                raise Exception(f"Deployment of {comp} failed: {e}")

    if is_dev and e2e_forcing != SKIP_E2E:
        print("=========================================================================")
        print("FANCY DEPLOY: now doing fake end-to-end test...")
        try:
            run_ssh_command(args.remote, "cd /home/admin/willow && python3 fake_end_to_end_test.py")
        except subprocess.CalledProcessError as e:
            print(f"Fake end-to-end test failed: {e}")
            if e2e_forcing != IGNORE_E2E:
                raise Exception(f"Fake end-to-end test failed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Fancy Willow deployment")
    parser.add_argument("remote", help="Remote user@host")
    parser.add_argument("--dev", action="store_true", help="Force development mode")
    parser.add_argument("--force", action="store_true", help="Force full deployment")
    parser.add_argument("--nolint", action="store_true", help="Skip pylint")
    parser.add_argument("--ignore-e2e", action="store_true", help="Don't revert if e2e test fails")
    parser.add_argument("--force-e2e", action="store_true", help="Run e2e test even if no change")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip e2e test")
    parser.add_argument("--push-only", action="store_true", help="rsync files with no lint/tests/deployment")
    args = parser.parse_args()

    remote_target = args.remote
    if ":" in remote_target and not remote_target.startswith('['):
        remote_target = f"[{remote_target}]" # IPv6 addr
    is_ipv6 = ":" in remote_target and remote_target.startswith('[')
    # minor HACK: currently, prod is a raw IPv6. so if not raw IPv6, assume dev.
    is_dev = args.dev or not is_ipv6

    if args.push_only:
        run_rsync(remote_target)
        sys.exit(0)

    # Dry-run to get changed files
    changed_files = dry_run_rsync(remote_target)
    print(f"Dry-run changed_files:\n {changed_files}\n\n")

    # Determine which components need deployment
    to_deploy = {}
    for comp, config in COMPONENTS.items():
        changed = args.force
        for path in config["paths"]:
            if any(f == path or f.startswith(path + '/') for f in changed_files):
                changed = True
        to_deploy[comp] = changed

    # Exit if nothing to deploy
    if not any(to_deploy.values()):
        try:
            # Force deployment if frontend isn't running (minor HACK; what if core/herder aren't
            # running? but this is really mostly for the case of a freshly restarted VM).
            remote_no_username = remote_target.split('@')[1]
            response = requests.get(f'http://{remote_no_username}:8008/health', timeout=3)
            response.raise_for_status()
            if not args.force_e2e:
                print("No changes detected. Deployment skipped.")
                return
        except Exception:
            print("Frontend not running, deploying all 3.")
            to_deploy['frontend'] = True
            to_deploy['core'] = True
            to_deploy['the_widdler'] = True

    if args.nolint:
        print('SKIPPING ALL PYLINT!!!')
        to_deploy['fe_lint'] = False
        to_deploy['core_lint'] = False
        to_deploy['widdler_lint'] = False

    # Run local linting for components that need it
    if to_deploy['fe_lint']:
        print("Running local frontend lint...")
        cmd = COMPONENTS['fe_lint']['local_lint_cmd']
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("Frontend lint failed!")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)
        else:
            print("Frontend lint passed.")

    if to_deploy['core_lint']:
        print("Running local core lint...")
        cmd = COMPONENTS['core_lint']['local_lint_cmd']
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("Core lint failed!")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)
        else:
            print("Core lint passed.")

    if to_deploy['widdler_lint']:
        print("Running local widdler lint...")
        cmd = COMPONENTS['widdler_lint']['local_lint_cmd']
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print("Widdler lint failed!")
            print(result.stdout)
            print(result.stderr)
            sys.exit(1)
        else:
            print("Widdler lint passed.")

    # Backup before rsync
    backup_dir = f"willow_backup_{int(time.time())}"
    exclude_str = " ".join([f"--exclude='{pattern}'" for pattern in EXCLUDE_PATTERNS])
    backup_cmd = f"rsync -a --delete {exclude_str} /home/admin/willow/ /tmp/{backup_dir}/"
    print(f"Backing up current remote state to {backup_dir}...")
    try:
        run_ssh_command(args.remote, backup_cmd)
    except subprocess.CalledProcessError as e:
        print(f"Backup failed: {e}")
        sys.exit(1)

    e2e_forcing = NORMAL_E2E
    if args.ignore_e2e:
        e2e_forcing = IGNORE_E2E
    if args.force_e2e:
        e2e_forcing = FORCE_E2E
    if args.skip_e2e:
        e2e_forcing = SKIP_E2E

    try:
        deploy(remote_target, args, is_dev, e2e_forcing, to_deploy)
    except Exception as e:
        print(f"Deployment failed: {e}")
        print("Restoring from backup...")
        try:
            restore_cmd = f"rsync -a --delete {exclude_str} /tmp/{backup_dir}/ /home/admin/willow/ && rm -rf /tmp/{backup_dir}"
            run_ssh_command(args.remote, restore_cmd)
        except subprocess.CalledProcessError as restore_err:
            print(f"Restore failed: {restore_err}")
        sys.exit(1)

    # Clean up backup after successful deployment
    print("Cleaning up backup...")
    try:
        run_ssh_command(args.remote, f"rm -rf /tmp/{backup_dir}")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Backup cleanup failed: {e}")

if __name__ == "__main__":
    main()
