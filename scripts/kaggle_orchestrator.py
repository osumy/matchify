"""Automated Kaggle dataset and kernel manager for Matchify."""

import argparse
import json
import os
from pathlib import Path
import shutil
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
    # Use stdout/stderr redirection or communicate to prevent pipe buffer deadlocks
    try:
        proc = subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        stdout, stderr = proc.communicate(timeout=180)
        return proc.returncode, (stdout or "") + "\n" + (stderr or "")
    except subprocess.TimeoutExpired:
        proc.kill()
        return 1, "Command timed out after 180s"


def upload_dataset(shard_idx: int, staging_dir: Path, shards_dir: Path) -> str:
    slug = f"matchify-cafochino-shard{shard_idx}"
    ds_id = f"{KAGGLE_USERNAME}/{slug}"
    ds_dir = staging_dir / f"dataset_shard{shard_idx}"
    ds_dir.mkdir(parents=True, exist_ok=True)

    # Find shard file
    shard_files = list(shards_dir.glob(f"cafochino_shard_{shard_idx:02d}_*.db"))
    if not shard_files:
        raise FileNotFoundError(f"Shard {shard_idx} file not found in {shards_dir}")

    shard_file = shard_files[0]
    dst_db = ds_dir / shard_file.name
    if not dst_db.exists():
        shutil.copy2(shard_file, dst_db)

    meta = {
        "title": f"Matchify Cafochino Shard {shard_idx}",
        "id": ds_id,
        "licenses": [{"name": "CC0-1.0"}]
    }
    with open(ds_dir / "dataset-metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"📦 Uploading dataset '{ds_id}'...", flush=True)
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "datasets", "create", "-p", str(ds_dir), "-q"])
    print(out.strip(), flush=True)
    if "already exists" in out.lower() or "please choose another name" in out.lower():
        print(f"ℹ️ Dataset already exists, versioning...", flush=True)
        code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "datasets", "version", "-p", str(ds_dir), "-m", "Update shard db", "-q"])
        print(out.strip(), flush=True)

    return ds_id


def check_status(kernel_id: str) -> str:
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "kernels", "status", kernel_id])
    for line in out.splitlines():
        if "has status" in line:
            return line.split("has status")[-1].strip().strip('"')
    if "Cannot access kernel" in out or "denied" in out or "not found" in out.lower():
        return "NOT_STARTED"
    return out.strip()


def push_kernel(shard_idx: int, platform: str, staging_dir: Path, notebook_path: Path) -> str:
    kernel_slug = f"matchify-{platform}-shard{shard_idx}"
    kernel_id = f"{KAGGLE_USERNAME}/{kernel_slug}"
    dataset_id = f"{KAGGLE_USERNAME}/matchify-cafochino-shard{shard_idx}"

    k_dir = staging_dir / f"kernel_{platform}_shard{shard_idx}"
    k_dir.mkdir(parents=True, exist_ok=True)

    dst_nb = k_dir / notebook_path.name
    shutil.copy2(notebook_path, dst_nb)

    meta = {
        "id": kernel_id,
        "title": kernel_slug,
        "code_file": notebook_path.name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": False,
        "enable_tpu": False,
        "enable_internet": True,
        "dataset_sources": [dataset_id]
    }
    with open(k_dir / "kernel-metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"🚀 Pushing kernel '{kernel_id}' (using dataset {dataset_id})...", flush=True)
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "kernels", "push", "-p", str(k_dir)])
    print(out.strip(), flush=True)
    return kernel_id


def download_output(kernel_id: str, dest_dir: Path) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd([PYTHON_BIN, "-m", "kaggle", "kernels", "output", kernel_id, "-p", str(dest_dir)])
    print(out.strip())
    dbs = list(dest_dir.glob("*.db"))
    return len(dbs) > 0


def main():
    parser = argparse.ArgumentParser(description="Kaggle Multi-Shard Orchestrator")
    parser.add_argument("--platform", choices=["neshan", "balad", "gmaps"], default="neshan")
    parser.add_argument("--action", choices=["status", "push_batch", "download", "push_one"], default="status")
    parser.add_argument("--shard", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4, help="Number of concurrent kernels to launch")

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    staging = project_root / "kaggle_staging"
    shards_dir = project_root / "data" / "shards"
    nb_path = project_root / "notebooks" / f"matchify_{args.platform}_kaggle.ipynb"

    if args.action == "status":
        print("=" * 60)
        print(f"📊 KAGGLE KERNEL STATUS FOR [{args.platform.upper()}] (Shards 1-8)")
        print("=" * 60)
        for i in range(1, 9):
            kid = f"{KAGGLE_USERNAME}/matchify-{args.platform}-shard{i}"
            st = check_status(kid)
            print(f"  Shard {i} ({kid}): {st}")
        print("=" * 60)

    elif args.action == "push_one":
        upload_dataset(args.shard, staging, shards_dir)
        push_kernel(args.shard, args.platform, staging, nb_path)

    elif args.action == "push_batch":
        # Check current running count
        running_cnt = 0
        statuses = {}
        for i in range(1, 9):
            kid = f"{KAGGLE_USERNAME}/matchify-{args.platform}-shard{i}"
            st = check_status(kid)
            statuses[i] = st
            if "RUNNING" in st or "QUEUED" in st:
                running_cnt += 1

        print(f"Current active Kaggle kernels: {running_cnt}/5")
        available_slots = max(0, 5 - running_cnt)

        launched = 0
        for i in range(1, 9):
            if launched >= available_slots:
                break
            st = statuses.get(i, "")
            if "COMPLETE" not in st and "RUNNING" not in st and "QUEUED" not in st:
                print(f"\n--- Launching Shard {i} ---")
                upload_dataset(i, staging, shards_dir)
                push_kernel(i, args.platform, staging, nb_path)
                launched += 1

    elif args.action == "download":
        results_dir = project_root / "data" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 9):
            kid = f"{KAGGLE_USERNAME}/matchify-{args.platform}-shard{i}"
            st = check_status(kid)
            if "COMPLETE" in st:
                td = results_dir / f"tmp_{args.platform}_{i}"
                if download_output(kid, td):
                    for db in td.glob("*.db"):
                        dest = results_dir / f"{args.platform}_shard_{i:02d}.db"
                        shutil.copy2(db, dest)
                        print(f"  ✅ Saved: {dest}")


if __name__ == "__main__":
    main()
