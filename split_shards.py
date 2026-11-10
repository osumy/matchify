"""Utility script to shard source cafe database for parallel multi-worker execution."""

import argparse
from pathlib import Path
import sqlite3
import sys

from loguru import logger


def split_database(
    source_db: str = r"c:\Users\amira\Desktop\DB\v3\1405-08-12-cafochino.db",
    output_dir: str = "data/shards",
    num_shards: int = 8
) -> list[Path]:
    src_path = Path(source_db)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if not src_path.exists():
        logger.error(f"Source database not found at '{source_db}'")
        sys.exit(1)

    print("=" * 60)
    print("✂️  MATCHIFY DATABASE SHARDER")
    print(f"Source: {src_path.resolve()}")
    print(f"Output Directory: {out_path.resolve()}")
    print(f"Target Shards: {num_shards}")
    print("=" * 60)

    conn_src = sqlite3.connect(src_path)
    cur_src = conn_src.cursor()

    cur_src.execute("""
        SELECT * FROM cafes_cafe
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY id ASC
    """)
    rows = cur_src.fetchall()
    col_names = [desc[0] for desc in cur_src.description]
    id_col_idx = col_names.index("id")

    # Fetch table creation SQL
    cur_src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cafes_cafe'")
    table_sql = cur_src.fetchone()[0]
    conn_src.close()

    total_records = len(rows)
    print(f"Total valid venues to shard: {total_records}")

    cols_str = ", ".join([f'"{c}"' for c in col_names])
    placeholders = ", ".join(["?"] * len(col_names))

    base_size = total_records // num_shards
    remainder = total_records % num_shards

    created_shards = []
    current_idx = 0

    for shard_idx in range(1, num_shards + 1):
        shard_len = base_size + (1 if shard_idx <= remainder else 0)
        shard_rows = rows[current_idx : current_idx + shard_len]
        current_idx += shard_len

        min_id = shard_rows[0][id_col_idx]
        max_id = shard_rows[-1][id_col_idx]

        shard_filename = f"cafochino_shard_{shard_idx:02d}_of_{num_shards:02d}_{len(shard_rows)}cafes.db"
        shard_file_path = out_path / shard_filename

        if shard_file_path.exists():
            shard_file_path.unlink()

        conn_shard = sqlite3.connect(shard_file_path)
        cur_shard = conn_shard.cursor()

        cur_shard.execute(table_sql)
        cur_shard.executemany(f"INSERT INTO cafes_cafe ({cols_str}) VALUES ({placeholders})", shard_rows)

        conn_shard.commit()
        conn_shard.close()

        created_shards.append(shard_file_path)
        print(f"  ✅ Shard {shard_idx:02d}/{num_shards:02d}: {shard_filename} (ID range: {min_id} - {max_id}, {len(shard_rows)} cafes)")

    print("=" * 60)
    print(f"Successfully created {len(created_shards)} database shards in '{output_dir}'.")
    return created_shards


def main():
    parser = argparse.ArgumentParser(description="Split source cafe database into shards for Kaggle/local execution")
    parser.add_argument("--source-db", type=str, default=r"c:\Users\amira\Desktop\DB\v3\1405-08-12-cafochino.db")
    parser.add_argument("--output-dir", type=str, default="data/shards")
    parser.add_argument("--num-shards", type=int, default=8)

    args = parser.parse_args()
    split_database(source_db=args.source_db, output_dir=args.output_dir, num_shards=args.num_shards)


if __name__ == "__main__":
    main()
