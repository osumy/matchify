"""Merges completed shard databases into a master database and exports Strict and Loose databases."""

import argparse
import glob
from pathlib import Path
import sqlite3
import sys

from loguru import logger
from src.db import export_strict_and_loose


def merge_databases(
    shard_pattern: str,
    master_output_db: str,
    strict_output_db: str,
    loose_output_db: str,
    platform: str
) -> dict:
    shard_files = sorted(glob.glob(shard_pattern, recursive=True))

    if not shard_files:
        logger.error(f"No shard databases found matching pattern: '{shard_pattern}'")
        return {}

    print("=" * 60)
    print(f"🔄 MATCHIFY SHARD MERGER [{platform.upper()}]")
    print(f"Shards Found: {len(shard_files)}")
    print(f"Master Output: {master_output_db}")
    print(f"Strict Output: {strict_output_db}")
    print(f"Loose Output:  {loose_output_db}")
    print("=" * 60)

    master_path = Path(master_output_db)
    master_path.parent.mkdir(parents=True, exist_ok=True)
    if master_path.exists():
        master_path.unlink()

    # Read schema from first shard
    conn_first = sqlite3.connect(shard_files[0])
    cur_first = conn_first.cursor()
    cur_first.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='places'")
    places_sql = cur_first.fetchone()[0]
    conn_first.close()

    conn_master = sqlite3.connect(master_path)
    cur_master = conn_master.cursor()
    cur_master.execute(places_sql)
    conn_master.commit()

    total_inserted = 0
    shards_processed = 0

    for shard in shard_files:
        try:
            conn_s = sqlite3.connect(shard)
            cur_s = conn_s.cursor()
            cur_s.execute("SELECT * FROM places")
            rows = cur_s.fetchall()
            if rows:
                placeholders = ",".join(["?"] * len(rows[0]))
                cur_master.executemany(f"INSERT OR REPLACE INTO places VALUES ({placeholders})", rows)
                conn_master.commit()
                total_inserted += len(rows)
            shards_processed += 1
            print(f"  Merged {len(rows)} records from: {Path(shard).name}")
            conn_s.close()
        except Exception as e:
            logger.error(f"Error reading shard {shard}: {e}")

    # Compute comprehensive analytics from master
    cur_master.execute("""
        SELECT 
            COUNT(*),
            SUM(CASE WHEN match_tier = 'strict' THEN 1 ELSE 0 END),
            SUM(CASE WHEN match_tier = 'loose' THEN 1 ELSE 0 END),
            SUM(CASE WHEN match_tier = 'none' THEN 1 ELSE 0 END),
            AVG(CASE WHEN match_tier != 'none' THEN distance_meters ELSE NULL END),
            AVG(CASE WHEN match_tier != 'none' THEN name_score ELSE NULL END),
            SUM(CASE WHEN phone_matched = 1 THEN 1 ELSE 0 END)
        FROM places
    """)
    row = cur_master.fetchone()
    total = row[0] or 0
    strict = row[1] or 0
    loose = row[2] or 0
    unmatched = row[3] or 0
    avg_dist = row[4] or 0.0
    avg_score = row[5] or 0.0
    phone_matches = row[6] or 0

    conn_master.close()

    # Split into strict and loose DBs
    strict_cnt, loose_cnt = export_strict_and_loose(
        master_db_path=master_output_db,
        strict_db_path=strict_output_db,
        loose_db_path=loose_output_db
    )

    print("\n" + "=" * 60)
    print(f"🎉 MERGE & EXPORT COMPLETE [{platform.upper()}]")
    print("=" * 60)
    print(f"Total Shards Merged: {shards_processed}")
    print(f"Total Venues in Master: {total}")
    print(f"  ✅ Strict (~99% confidence): {strict} ({strict/max(1, total)*100:.1f}%) -> {strict_output_db}")
    print(f"  🔍 Loose (Human review queue): {loose} ({loose/max(1, total)*100:.1f}%) -> {loose_output_db}")
    print(f"  ❌ Unmatched: {unmatched} ({unmatched/max(1, total)*100:.1f}%)")
    print(f"  📞 Phone Matched: {phone_matches}")
    print(f"  📏 Avg Distance for Matches: {avg_dist:.1f} meters")
    print(f"  🎯 Avg Name Score for Matches: {avg_score:.3f}")
    print("=" * 60 + "\n")

    return {
        "total": total,
        "strict": strict,
        "loose": loose,
        "unmatched": unmatched,
        "avg_dist": avg_dist,
        "avg_score": avg_score,
        "phone_matches": phone_matches
    }


def main():
    parser = argparse.ArgumentParser(description="Merge Matchify shards and export Strict & Loose databases")
    parser.add_argument("--platform", choices=["neshan", "balad", "gmaps"], required=True, help="Map platform name")
    parser.add_argument("--shard-pattern", type=str, required=True, help="Glob pattern for shards (e.g. 'data/results/neshan_shard_*.db')")
    parser.add_argument("--master-db", type=str, default=None, help="Output path for master DB")
    parser.add_argument("--strict-db", type=str, default=None, help="Output path for strict DB")
    parser.add_argument("--loose-db", type=str, default=None, help="Output path for loose DB")

    args = parser.parse_args()

    platform = args.platform.lower()
    master_db = args.master_db or f"data/master/{platform}_master.db"
    strict_db = args.strict_db or f"data/master/{platform}_matched_strict.db"
    loose_db = args.loose_db or f"data/master/{platform}_matched_loose.db"

    merge_databases(
        shard_pattern=args.shard_pattern,
        master_output_db=master_db,
        strict_output_db=strict_db,
        loose_output_db=loose_db,
        platform=platform
    )


if __name__ == "__main__":
    main()
