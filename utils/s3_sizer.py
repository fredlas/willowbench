#!/usr/bin/env python3
import sys
import json
import boto3
from botocore.config import Config as Boto3Config
import argparse
from urllib.parse import urlparse

def get_s3_file_sizes(s3_urls, aws_region_name):
    s3 = boto3.client('s3', region_name=aws_region_name, config=Boto3Config(use_dualstack_endpoint = True))
    sizes = {}
    for url in s3_urls:
        try:
            parsed_url = urlparse(url)
            bucket = parsed_url.netloc
            key = parsed_url.path.lstrip('/')
            # Use us-east-1 as a default region for the client. S3 is global for bucket names,
            # but the client needs a region. This is fine for head_object.
            response = s3.head_object(Bucket=bucket, Key=key)
            sizes[url] = response['ContentLength']
        except Exception as e:
            print(f"Error getting size for {url}: {e}", file=sys.stderr)
            # We don't want to fail the whole batch, just return what we can.
            # The WDL side will wait for the missing ones.
    return sizes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Get sizes of S3 objects')
    parser.add_argument("--aws-region", required=True, help="AWS region to query")
    parser.add_argument("s3_urls", nargs='*', help="List of S3 URLs to size")

    args = parser.parse_args()

    if len(args.s3_urls) == 0:
        print(json.dumps({}))
        sys.exit(0)

    sizes = get_s3_file_sizes(args.s3_urls, args.aws_region)
    print(json.dumps(sizes))
