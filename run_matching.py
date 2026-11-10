"""Unified CLI runner for executing Matchify against Neshan, Balad, or Google Maps."""

import argparse
import asyncio
from datetime import datetime
import sys
from pathlib import Path
from typing import Optional

from loguru import logger
from tqdm import tqdm

from src.config import NESHAN_CONFIG, BALAD_CONFIG, GMAPS_CONFIG, PlatformConfig
from src.db import DatabaseManager, CafeRecord
from src.matcher import (
    MatchTier,
    MatchResult,
    rank_and_select_best_match,
    evaluate_candidate
)
from src.platforms import NeshanClient, BaladClient, GoogleMapsClient, BasePlatformClient


def get_client(platform: str, config: PlatformConfig, master_db: Optional[str] = None) -> BasePlatformClient:
    p = platform.lower()
    if p == "neshan":
        return NeshanClient(config=config)
    elif p == "balad":
        return BaladClient(config=config)
    elif p == "gmaps":
        return GoogleMapsClient(master_db_path=master_db)
    else:
        raise ValueError(f"Unsupported platform: {platform}. Choose 'neshan', 'balad', or 'gmaps'.")


async def run_platform_pipeline(
    platform: str,
    source_db: str,
    output_db: str,
    limit: Optional[int] = None,
    enrich: bool = True,
    batch_size: int = 25,
    master_db: Optional[str] = None
) -> dict:
    logger.info(f"Initializing Matchify pipeline for platform: [{platform.upper()}]")
    logger.info(f"Source DB: {source_db}")
    logger.info(f"Output DB: {output_db}")

    db_manager = DatabaseManager(platform=platform, output_db_path=output_db)
    pending_cafes = db_manager.load_pending_cafes(source_db_path=source_db, output_db_path=output_db)

    if limit and limit > 0:
        pending_cafes = pending_cafes[:limit]

    total = len(pending_cafes)
    logger.info(f"Loaded {total} pending cafes to match.")

    if total == 0:
        logger.success("All cafes in this shard are already matched!")
        stats = db_manager.update_checkpoint(0)
        return stats

    # Select config
    if platform.lower() == "neshan":
        config = NESHAN_CONFIG
    elif platform.lower() == "balad":
        config = BALAD_CONFIG
    else:
        config = GMAPS_CONFIG
    config.enrich_places = enrich

    client = get_client(platform, config, master_db)

    strict_count = 0
    loose_count = 0
    unmatched_count = 0

    pbar = tqdm(pending_cafes, desc=f"Matching {platform.capitalize()}", unit="cafe")

    try:
        for idx, cafe in enumerate(pbar, 1):
            candidates = await client.search_candidates(cafe)

            if candidates:
                best_match = rank_and_select_best_match(
                    target_name=cafe.name,
                    target_lat=cafe.latitude,
                    target_lng=cafe.longitude,
                    target_phone=cafe.phone,
                    candidates=candidates
                )

                # Enrich place if matched and candidate has missing info
                if enrich and best_match.is_match and best_match.candidate:
                    enriched_cand = await client.enrich_candidate(best_match.candidate)
                    best_match = evaluate_candidate(
                        target_name=cafe.name,
                        target_lat=cafe.latitude,
                        target_lng=cafe.longitude,
                        target_phone=cafe.phone,
                        candidate=enriched_cand
                    )

                db_manager.save_place_match(cafe, best_match)

                if best_match.match_tier == MatchTier.STRICT:
                    strict_count += 1
                elif best_match.match_tier == MatchTier.LOOSE:
                    loose_count += 1
                else:
                    unmatched_count += 1
            else:
                rejected = MatchResult(
                    is_match=False,
                    match_tier=MatchTier.REJECTED,
                    match_rule="NO_SEARCH_RESULTS",
                    composite_score=0.0,
                    name_score=0.0,
                    distance_meters=float('inf'),
                    phone_matched=False,
                    rejection_reason="No search results returned from API"
                )
                db_manager.save_place_match(cafe, rejected)
                unmatched_count += 1

            pbar.set_postfix({
                "Strict": strict_count,
                "Loose": loose_count,
                "Unmatched": unmatched_count,
                "Match%": f"{(strict_count + loose_count) / idx * 100:.1f}%"
            })

            if idx % batch_size == 0 or idx == total:
                db_manager.update_checkpoint(cafe.id)

    finally:
        await client.close()

    stats = db_manager.update_checkpoint(pending_cafes[-1].id if pending_cafes else 0)

    print("\n" + "=" * 60)
    print(f"📊 MATCHIFY {platform.upper()} RUN COMPLETE")
    print("=" * 60)
    print(f"Total Processed: {stats.get('total', 0)}")
    print(f"Strict Matches (~99% certainty): {stats.get('strict', 0)} ({stats.get('strict', 0)/max(1, stats.get('total', 1))*100:.1f}%)")
    print(f"Loose Matches (Human Review Queue): {stats.get('loose', 0)} ({stats.get('loose', 0)/max(1, stats.get('total', 1))*100:.1f}%)")
    print(f"Unmatched / Not Found: {stats.get('unmatched', 0)} ({stats.get('unmatched', 0)/max(1, stats.get('total', 1))*100:.1f}%)")
    print(f"Output Database: {output_db}")
    print("=" * 60 + "\n")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Matchify Multi-Platform Map Matching Engine")
    parser.add_argument("--platform", choices=["neshan", "balad", "gmaps"], required=True, help="Target map platform")
    parser.add_argument("--source-db", type=str, default="data/cafochino.db", help="Path to input SQLite database (or shard)")
    parser.add_argument("--output-db", type=str, required=True, help="Path to output SQLite database")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit on number of venues to process")
    parser.add_argument("--no-enrich", action="store_true", help="Skip secondary enrichment call for faster processing")
    parser.add_argument("--master-db", type=str, default=None, help="Pre-scraped master DB for Google Maps")

    args = parser.parse_args()

    asyncio.run(run_platform_pipeline(
        platform=args.platform,
        source_db=args.source_db,
        output_db=args.output_db,
        limit=args.limit,
        enrich=not args.no_enrich,
        master_db=args.master_db
    ))


if __name__ == "__main__":
    main()
