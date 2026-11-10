"""Google Maps platform adapter and existing master DB importer."""

from pathlib import Path
import sqlite3
from typing import List, Optional, Dict, Any

from loguru import logger

from src.db import CafeRecord
from src.matcher import MatchCandidate, MatchResult, evaluate_candidate
from src.platforms.base import BasePlatformClient


class GoogleMapsClient(BasePlatformClient):
    """Adapter for Google Maps places data and evaluation."""

    def __init__(self, master_db_path: Optional[str] = None):
        self.master_db_path = master_db_path

    async def search_candidates(self, cafe: CafeRecord) -> List[MatchCandidate]:
        """Lookups candidate from pre-scraped Google Maps master DB if available."""
        if not self.master_db_path or not Path(self.master_db_path).exists():
            return []

        conn = sqlite3.connect(self.master_db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT place_id, title, scraped_lat, scraped_lng, address, phone, category_name, google_maps_url, total_score, reviews_count
            FROM places
            WHERE cafe_id = ?
        """, (cafe.id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return []

        pid, title, lat, lng, addr, phone, cat, url, score, revs = row
        cand = MatchCandidate(
            candidate_id=pid or f"gmaps_{cafe.id}",
            title=title or cafe.name,
            latitude=lat,
            longitude=lng,
            address=addr,
            phone=phone,
            category=cat,
            url=url,
            raw_data={
                "rating": score,
                "reviews_count": revs
            }
        )
        return [cand]

    async def enrich_candidate(self, candidate: MatchCandidate) -> MatchCandidate:
        return candidate

    async def close(self) -> None:
        pass
