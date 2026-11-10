"""Kaggle Orchestrator for Matchify: pushes shards, launches kernels, tracks status, and downloads results."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

KAGGLE_USERNAME = "amiraliaraghi"
API_TOKEN = "KGAT_836740cc96f6d44b36bc678c98af8889"
PYTHON_BIN = sys.executable


def set_kaggle_env():
    os.environ["KAGGLE_API_TOKEN"] = API_TOKEN
    os.environ["KAGGLE_USERNAME"] = KAGGLE_USERNAME


def run_cmd(cmd_list: list[str]) -> tuple[int, str]:
    set_kaggle_env()
    proc = subprocess.run(cmd_list, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def check_kernel_status(kernel_id: str) -> str:
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "kernels", "status", kernel_id])
    for line in out.splitlines():
        if "has status" in line:
            return line.split("has status")[-1].strip().strip('"')
    return out.strip()


def download_kernel_output(kernel_id: str, dest_dir: Path) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "kernels", "output", kernel_id, "-p", str(dest_dir)])
    print(out.strip())
    # Check if any db file was downloaded
    dbs = list(dest_dir.glob("*.db"))
    return len(dbs) > 0


def status_all(platform: str, total_shards: int = 8):
    print("=" * 60)
    print(f"📊 KAGGLE STATUS FOR [{platform.upper()}] SHARDS (1 to {total_shards})")
    print("=" * 60)
    for i in range(1, total_shards + 1):
        kid = f"{KAGGLE_USERNAME}/matchify-{platform}-shard{i}"
        st = check_kernel_status(kid)
        print(f"  Shard {i} ({kid}): {st}")
    print("=" * 60)


def download_all(platform: str, total_shards: int = 8, results_dir: str = "data/results"):
    out_base = Path(results_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📥 DOWNLOADING COMPLETED OUTPUTS FOR [{platform.upper()}]")
    print("=" * 60)

    downloaded = []
    for i in range(1, total_shards + 1):
        kid = f"{KAGGLE_USERNAME}/matchify-{platform}-shard{i}"
        target_dir = out_base / f"{platform}_shard_{i}"
        print(f"Checking Shard {i} ({kid})...")
        st = check_kernel_status(kid)
        if "COMPLETE" in st:
            success = download_kernel_output(kid, target_dir)
            if success:
                # Rename/copy db to clean name
                for db in target_dir.glob("*.db"):
                    dest = out_base / f"{platform}_shard_{i:02d}.db"
                    import shutil
                    shutil.copy2(db, dest)
                    downloaded.append(str(dest))
                    print(f"  ✅ Saved: {dest}")
        else:
            print(f"  ⏳ Status is: {st}")

    print("=" * 60)
    print(f"Downloaded {len(downloaded)} shards ready for merging.")


def main():
    parser = argparse.ArgumentParser(description="Matchify Kaggle Manager")
    parser.add_argument("--action", choices=["status", "download"], required=True)
    parser.add_argument("--platform", choices=["neshan", "balad", "gmaps"], default="neshan")
    parser.add_argument("--shards", type=int, default=8)

    args = parser.parse_args()

    if args.action == "status":
        status_all(platform=args.platform, total_shards=args.shards)
    elif args.action == "download":
        download_all(platform=args.platform, total_shards=args.shards)


if __name__ == "__main__":
    main()
