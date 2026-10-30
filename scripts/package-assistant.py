"""
Builds the assistant's code zip for AgentCore Runtime (direct code
deployment), uploads it to S3, and prints the parameter overrides for
`sam deploy`: the environment's own (samconfig.toml) plus the zip's location.

    sam deploy --config-env dev \\
        --parameter-overrides "$(uv run python scripts/package-assistant.py --config-env dev)"

`sam build` can't do this: the assistant is not a Lambda function. AgentCore
runs Linux on arm64, so dependencies are installed as arm64 wheels whatever
machine this runs on, at the exact versions in uv.lock. The S3 key is the
zip's hash, so CloudFormation updates the agent only when the code changed.
The bucket defaults to the one `sam deploy` already uses (resolve_s3).

Progress goes to stderr; stdout is only the overrides.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "assistant"
SOURCES = {  # path in the zip -> source in the repository
    "main.py": ROOT / "modules/assistant/main.py",
    "assistant": ROOT / "modules/assistant/src/assistant",
    "shared": ROOT / "layers/shared/layer/shared",
}
MAX_ZIPPED, MAX_UNZIPPED = 250 * 2**20, 750 * 2**20   # AgentCore's limits for a code zip


def log(message: str) -> None:
    print(message, file=sys.stderr)


def portable_launcher(script: bytes) -> bytes:
    """
    A console script made to run with the runtime's python3 rather than the
    Python that installed it. Installers write one of two headers: a shebang
    naming that Python, or, when its path is too long for a shebang (about
    127 bytes, as in a deep build directory), a /bin/sh header that execs it:
        #!/bin/sh
        '''exec' '/long/path/bin/python' "$0" "$@"
        ' '''
    Either becomes "#!/usr/bin/env python3"; anything else is left alone.
    """
    lines = script.split(b"\n")
    if lines[0].startswith(b"#!") and b"python" in lines[0]:
        return b"\n".join([b"#!/usr/bin/env python3", *lines[1:]])
    if lines[0] == b"#!/bin/sh" and len(lines) > 2 and lines[1].startswith(b"'''exec'") and lines[2] == b"' '''":
        return b"\n".join([b"#!/usr/bin/env python3", *lines[3:]])
    return script


def build() -> Path:
    shutil.rmtree(BUILD, ignore_errors=True)
    package = BUILD / "package"
    requirements = BUILD / "requirements.txt"
    package.mkdir(parents=True)
    log("Exporting dependencies from uv.lock")
    subprocess.run(["uv", "export", "--locked", "--package", "saas-assistant", "--no-dev",
                    "--no-emit-workspace", "--no-hashes", "--no-header", "--quiet", "-o", str(requirements)],
                   check=True, cwd=ROOT)
    log("Installing them as Linux arm64 wheels")
    # PYTHON_3_13 runs on Amazon Linux 2023 (glibc 2.34). AWS's example says
    # manylinux2014 (glibc 2.17), but some wheels (numpy 2.5) need 2.28.
    subprocess.run(["uv", "pip", "install", "--quiet", "--python-platform", "aarch64-manylinux_2_34",
                    "--python-version", "3.13", "--only-binary=:all:", "--target", str(package),
                    "-r", str(requirements)], check=True, cwd=ROOT)
    (package / ".lock").unlink(missing_ok=True)  # uv's, not ours
    # Launchers such as bin/opentelemetry-instrument (the entry point) point at this machine's Python.
    for script in (package / "bin").glob("*"):
        script.write_bytes(portable_launcher(script.read_bytes()))
    for name, source in SOURCES.items():
        if source.is_dir():
            shutil.copytree(source, package / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(source, package / name)
    return package


def zip_package(package: Path) -> Path:
    """Same files in, same bytes out: fixed timestamps and order, the permissions AgentCore needs."""
    archive = BUILD / "assistant.zip"
    unzipped = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            info = zipfile.ZipInfo(path.relative_to(package).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            data = path.read_bytes()
            unzipped += len(data)
            zf.writestr(info, data)
    zipped = archive.stat().st_size
    log(f"Zip: {zipped / 2**20:.0f} MB ({unzipped / 2**20:.0f} MB unzipped)")
    if zipped > MAX_ZIPPED or unzipped > MAX_UNZIPPED:
        sys.exit("Too big for direct code deployment (250 MB zipped, 750 MB unzipped): use a container image")
    return archive


def sam_bucket(region: str) -> str:
    """The bucket `sam deploy` creates for its artifacts when resolve_s3 = true."""
    try:
        stack = boto3.client("cloudformation", region_name=region).describe_stacks(
            StackName="aws-sam-cli-managed-default")["Stacks"][0]
    except ClientError as e:
        if "does not exist" not in str(e):
            raise
        sys.exit(f"No sam deploy has run in this account and region ({region}) yet, so there is no bucket for "
                 "the zip. Deploy once without the assistant (sam deploy --config-env <env>), then run this "
                 "again, or pass --bucket.")
    return next(o["OutputValue"] for o in stack["Outputs"] if o["OutputKey"] == "SourceBucket")


def upload(archive: Path, bucket: str, region: str) -> str:
    key = f"assistant/{hashlib.sha256(archive.read_bytes()).hexdigest()[:20]}.zip"
    s3 = boto3.client("s3", region_name=region)
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    try:
        s3.head_object(Bucket=bucket, Key=key, ExpectedBucketOwner=account)
        log(f"Unchanged: s3://{bucket}/{key}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
            raise
        s3.upload_file(str(archive), bucket, key, ExtraArgs={"ExpectedBucketOwner": account})
        log(f"Uploaded: s3://{bucket}/{key}")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config-env", required=True, help="dev or prod (samconfig.toml)")
    parser.add_argument("--bucket", help="default: the bucket sam deploy uses")
    args = parser.parse_args()

    config = tomllib.loads((ROOT / "samconfig.toml").read_text())
    region = config["default"]["global"]["parameters"]["region"]
    overrides = config[args.config_env]["deploy"]["parameters"]["parameter_overrides"]

    bucket = args.bucket or sam_bucket(region)
    key = upload(zip_package(build()), bucket, region)
    print(" ".join([*overrides, f"AssistantCodeBucket={bucket}", f"AssistantCodeKey={key}"]))


if __name__ == "__main__":
    main()
