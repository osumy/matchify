"""Unified multi-shard runner for Matchify.

Runs all 8 shards with controlled concurrency and automatic resuming.
If interrupted at any time (Ctrl+C, crash, reboot), re-running the same command
will automatically pick up right where it left off without losing any data.
"""

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PYTHON_BIN = sys.executable

def run_single_shard(shard_path: Path, platform: str, output_path: Path, enrich: bool = True):
    cmd = [
        PYTHON_BIN,
        "run_matching.py",
        "--platform", platform,
        "--source-db", str(shard_path),
        "--output-db", str(output_path),
    ]
    if not enrich:
        cmd.append("--no-enrich")

    print(f"🚀 Starting {platform.upper()} matching for: {shard_path.name}")
    start = time.time()
    proc = subprocess.run(cmd)
    elapsed = time.time() - start
    if proc.returncode == 0:
        print(f"✅ Finished {shard_path.name} in {elapsed:.1f}s -> {output_path.name}")
    else:
        print(f"❌ Error in {shard_path.name} (exit code: {proc.returncode})")
    return proc.returncode

def main():
    parser = argparse.ArgumentParser(description="Matchify Multi-Shard Local Runner")
    parser.add_argument("--platform", choices=["neshan", "balad"], default="neshan", help="Platform to match")
    parser.add_argument("--workers", type=int, default=3, help="Number of parallel shard workers (default: 3)")
    parser.add_argument("--no-enrich", action="store_true", help="Skip secondary enrichment for faster processing")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    shards_dir = project_root / "data" / "shards"
    results_dir = project_root / "data" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    shard_files = sorted(shards_dir.glob("cafochino_shard_*.db"))
    if not shard_files:
        print(f"❌ No shard files found in {shards_dir}")
        return

    print("=" * 65)
    print(f"🗺️  MATCHIFY MULTI-SHARD RUNNER: [{args.platform.upper()}]")
    print(f"📁 Shards found: {len(shard_files)}")
    print(f"⚙️  Concurrency: {args.workers} parallel workers")
    print(f"🔄 Resume Mode: Enabled (crash-safe, checkpoints after each cafe)")
    print("=" * 65 + "\n")

    tasks = []
    for idx, shard_file in enumerate(shard_files, 1):
        output_file = results_dir / f"{args.platform}_shard_{idx:02d}.db"
        tasks.append((shard_file, output_file))

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_single_shard, s_file, args.platform, o_file, not args.no_enrich): (s_file, o_file)
            for s_file, o_file in tasks
        }
        for future in as_completed(futures):
            future.result()

    print("\n" + "=" * 65)
    print(f"🎉 All {len(shard_files)} shards completed for [{args.platform.upper()}]!")
    print("👉 To merge results and export Strict / Loose databases, run:")
    print(f"   python merge_shards.py --platform {args.platform}")
    print("=" * 65)

if __name__ == "__main__":
    main()
