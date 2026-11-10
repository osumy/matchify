"""Database manager for SQLite storage, checkpointing, and strict/loose export."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Optional, List, Dict, Any

from src.matcher import MatchResult, MatchCandidate, MatchTier


@dataclass
class CafeRecord:
    id: int
    name: str
    city: str
    latitude: Optional[float]
    longitude: Optional[float]
    phone: Optional[str] = None
    address: Optional[str] = None
    neshan_address: Optional[str] = None
    instagram: Optional[str] = None
    website: Optional[str] = None


class DatabaseManager:
    def __init__(self, platform: str, output_db_path: str):
        self.platform = platform.lower()
        self.output_db_path = Path(output_db_path)
        self.output_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_output_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.output_db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_output_db(self) -> None:
        """Initializes tables for places and checkpoints."""
        with self._get_connection() as conn:
            cur = conn.cursor()

            # Generic place table capturing canonical identifiers across platforms
            cur.execute("""
            CREATE TABLE IF NOT EXISTS places (
                cafe_id INTEGER PRIMARY KEY,
                canonical_id TEXT,
                title TEXT,
                category_name TEXT,
                searched_name TEXT,
                searched_lat REAL,
                searched_lng REAL,
                matched_lat REAL,
                matched_lng REAL,
                address TEXT,
                phone TEXT,
                extra_url TEXT,
                rating REAL,
                rating_count INTEGER,
                place_url TEXT,
                match_tier TEXT,
                match_rule TEXT,
                name_score REAL,
                distance_meters REAL,
                composite_score REAL,
                phone_matched INTEGER DEFAULT 0,
                rejection_reason TEXT,
                extra_json TEXT,
                matched_at TEXT
            );
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                platform TEXT,
                last_cafe_id INTEGER,
                total_processed INTEGER DEFAULT 0,
                strict_count INTEGER DEFAULT 0,
                loose_count INTEGER DEFAULT 0,
                unmatched_count INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """)
            conn.commit()

    @staticmethod
    def load_pending_cafes(source_db_path: str, output_db_path: Optional[str] = None) -> List[CafeRecord]:
        """Loads pending cafe records from the source database, filtering out already processed ones."""
        src_conn = sqlite3.connect(source_db_path)
        src_cur = src_conn.cursor()

        processed_ids = set()
        if output_db_path and Path(output_db_path).exists():
            out_conn = sqlite3.connect(output_db_path)
            out_cur = out_conn.cursor()
            try:
                out_cur.execute("SELECT cafe_id FROM places")
                processed_ids = {r[0] for r in out_cur.fetchall()}
            except sqlite3.OperationalError:
                pass
            finally:
                out_conn.close()

        # Check column names in cafes_cafe or places
        src_cur.execute("PRAGMA table_info(cafes_cafe)")
        cols = {c[1] for c in src_cur.fetchall()}

        if "id" in cols:
            has_neshan_addr = "neshan_address" in cols
            has_instagram = "instagram" in cols
            has_website = "website" in cols

            query = f"""
                SELECT id, name, city, latitude, longitude, phone, address
                {', neshan_address' if has_neshan_addr else ', NULL'}
                {', instagram' if has_instagram else ', NULL'}
                {', website' if has_website else ', NULL'}
                FROM cafes_cafe
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY id ASC
            """
            src_cur.execute(query)
            rows = src_cur.fetchall()
            cafes = []
            for r in rows:
                cid, name, city, lat, lng, phone, addr, n_addr, insta, web = r
                if cid not in processed_ids:
                    cafes.append(CafeRecord(
                        id=cid,
                        name=name.strip() if name else "",
                        city=city.strip() if city else "تهران",
                        latitude=lat,
                        longitude=lng,
                        phone=phone,
                        address=addr,
                        neshan_address=n_addr,
                        instagram=insta,
                        website=web
                    ))
            src_conn.close()
            return cafes

        src_conn.close()
        return []

    def save_place_match(self, cafe: CafeRecord, result: MatchResult) -> None:
        """Saves a matched or rejected place result into the database."""
        now_iso = datetime.now(timezone.utc).isoformat()
        cand = result.candidate

        canonical_id = cand.candidate_id if cand else None
        title = cand.title if cand else None
        cat = cand.category if cand else None
        m_lat = cand.latitude if cand else None
        m_lng = cand.longitude if cand else None
        addr = cand.address if cand else None
        phone = cand.phone if cand else None
        url = cand.url if cand else None
        raw_json = json.dumps(cand.raw_data or {}, ensure_ascii=False) if cand else "{}"

        # Extract extra info like rating or instagram if in raw_data
        rating = None
        rating_count = None
        extra_url = None

        if cand and cand.raw_data:
            rd = cand.raw_data
            rating = rd.get("rating")
            rating_count = rd.get("rating_count") or rd.get("votes_count") or rd.get("reviews_count")
            extra_url = rd.get("instagram") or rd.get("website")

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO places (
                cafe_id, canonical_id, title, category_name, searched_name,
                searched_lat, searched_lng, matched_lat, matched_lng, address,
                phone, extra_url, rating, rating_count, place_url,
                match_tier, match_rule, name_score, distance_meters,
                composite_score, phone_matched, rejection_reason,
                extra_json, matched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cafe.id,
                canonical_id,
                title,
                cat,
                cafe.name,
                cafe.latitude,
                cafe.longitude,
                m_lat,
                m_lng,
                addr,
                phone,
                extra_url,
                rating,
                rating_count,
                url,
                result.match_tier.value,
                result.match_rule,
                result.name_score,
                result.distance_meters if result.distance_meters != float('inf') else None,
                result.composite_score,
                1 if result.phone_matched else 0,
                result.rejection_reason,
                raw_json,
                now_iso
            ))
            conn.commit()

    def update_checkpoint(self, last_cafe_id: int) -> Dict[str, int]:
        """Calculates current match tallies and updates the checkpoint row."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    COUNT(*),
                    SUM(CASE WHEN match_tier = 'strict' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN match_tier = 'loose' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN match_tier = 'none' THEN 1 ELSE 0 END)
                FROM places
            """)
            row = cur.fetchone()
            total = row[0] or 0
            strict = row[1] or 0
            loose = row[2] or 0
            unmatched = row[3] or 0

            cur.execute("""
            INSERT OR REPLACE INTO checkpoints (
                id, platform, last_cafe_id, total_processed,
                strict_count, loose_count, unmatched_count, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """, (self.platform, last_cafe_id, total, strict, loose, unmatched, now_iso))
            conn.commit()

            return {
                "total": total,
                "strict": strict,
                "loose": loose,
                "unmatched": unmatched
            }


def export_strict_and_loose(
    master_db_path: str,
    strict_db_path: str,
    loose_db_path: str
) -> Tuple[int, int]:
    """Splits a consolidated master database into two separate standalone databases for Strict and Loose matches."""
    m_path = Path(master_db_path)
    s_path = Path(strict_db_path)
    l_path = Path(loose_db_path)

    s_path.parent.mkdir(parents=True, exist_ok=True)
    l_path.parent.mkdir(parents=True, exist_ok=True)

    if s_path.exists():
        s_path.unlink()
    if l_path.exists():
        l_path.unlink()

    conn_m = sqlite3.connect(m_path)
    cur_m = conn_m.cursor()

    # Get column definitions
    cur_m.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='places'")
    create_sql = cur_m.fetchone()[0]

    # Create tables in target dbs
    for target_path, tier_val in [(s_path, 'strict'), (l_path, 'loose')]:
        conn_t = sqlite3.connect(target_path)
        cur_t = conn_t.cursor()
        cur_t.execute(create_sql)

        cur_m.execute(f"SELECT * FROM places WHERE match_tier = '{tier_val}' ORDER BY cafe_id ASC")
        rows = cur_m.fetchall()
        if rows:
            placeholders = ",".join(["?"] * len(rows[0]))
            cur_t.executemany(f"INSERT INTO places VALUES ({placeholders})", rows)
        conn_t.commit()
        conn_t.close()

    cur_m.execute("SELECT COUNT(*) FROM places WHERE match_tier = 'strict'")
    s_cnt = cur_m.fetchone()[0]
    cur_m.execute("SELECT COUNT(*) FROM places WHERE match_tier = 'loose'")
    l_cnt = cur_m.fetchone()[0]

    conn_m.close()
    return s_cnt, l_cnt
