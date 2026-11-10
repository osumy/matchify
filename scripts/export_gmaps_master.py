"""Exports Google Maps matched places from Mapify into consolidated Strict and Loose SQLite databases."""

from pathlib import Path
import sqlite3
import sys

# Ensure matchify root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.matcher import MatchTier, evaluate_candidate, MatchCandidate, check_phone_match
from src.db import DatabaseManager, export_strict_and_loose
from src.normalizer import normalize_phone


def export_gmaps_dbs(
    matched_db: str = r"D:\src\mapify\data\merged_cafe_photos_matched.db",
    master_meta_db: str = r"D:\src\mapify\data\cafe_photos_master.db",
    source_db: str = r"c:\Users\amira\Desktop\DB\v3\1405-08-12-cafochino.db",
    out_master_db: str = "data/master/gmaps_master.db",
    out_strict_db: str = "data/master/gmaps_matched_strict.db",
    out_loose_db: str = "data/master/gmaps_matched_loose.db"
):
    print("=" * 60)
    print("🌍 EXPORTING GOOGLE MAPS STRICT & LOOSE DATABASES")
    print(f"Matched DB: {matched_db}")
    print(f"Master Metadata DB: {master_meta_db}")
    print(f"Source DB: {source_db}")
    print("=" * 60)

    # 1. Load original phone numbers from source DB
    conn_src = sqlite3.connect(source_db)
    cur_src = conn_src.cursor()
    cur_src.execute("SELECT id, name, phone, latitude, longitude FROM cafes_cafe")
    source_records = {r[0]: {"name": r[1], "phone": r[2], "lat": r[3], "lng": r[4]} for r in cur_src.fetchall()}
    conn_src.close()

    # 2. Load match scores from merged_cafe_photos_matched.db
    conn_matched = sqlite3.connect(matched_db)
    cur_matched = conn_matched.cursor()
    cur_matched.execute("""
        SELECT cafe_id, name_score, distance_meters, title, searched_name, google_maps_url, status
        FROM places
        WHERE status = 'matched'
    """)
    matched_scores = {r[0]: {
        "name_score": r[1],
        "distance_meters": r[2],
        "title": r[3],
        "searched_name": r[4],
        "url": r[5]
    } for r in cur_matched.fetchall()}
    conn_matched.close()

    # 3. Load metadata from cafe_photos_master.db
    conn_meta = sqlite3.connect(master_meta_db)
    cur_meta = conn_meta.cursor()
    cur_meta.execute("""
        SELECT 
            cafe_id, place_id, cid, fid, kgmid, title, category_name,
            scraped_lat, scraped_lng, address, phone, website,
            total_score, reviews_count, plus_code, google_maps_url, scraped_at
        FROM places
        WHERE status = 'matched'
    """)
    meta_rows = cur_meta.fetchall()
    conn_meta.close()

    # Prepare master output DB
    db_manager = DatabaseManager(platform="gmaps", output_db_path=out_master_db)

    strict_count = 0
    loose_count = 0
    phone_match_count = 0

    print(f"Processing {len(meta_rows)} matched venues...")

    with db_manager._get_connection() as conn_out:
        cur_out = conn_out.cursor()

        for row in meta_rows:
            (
                cid, pid, g_cid, fid, kgmid, title, category,
                s_lat, s_lng, address, phone, website,
                rating, reviews, plus_code, g_url, scraped_at
            ) = row

            scores = matched_scores.get(cid, {})
            name_score = scores.get("name_score", 1.0)
            distance = scores.get("distance_meters", 0.0)
            target_info = source_records.get(cid, {})

            target_phone = target_info.get("phone")
            phone_matched = check_phone_match(target_phone, phone)
            if phone_matched:
                phone_match_count += 1

            # Candidate object for matcher evaluation
            cand = MatchCandidate(
                candidate_id=pid or f"gmaps_{cid}",
                title=title or target_info.get("name", ""),
                latitude=s_lat,
                longitude=s_lng,
                address=address,
                phone=phone,
                category=category,
                url=g_url,
                raw_data={
                    "cid": g_cid,
                    "fid": fid,
                    "kgmid": kgmid,
                    "rating": rating,
                    "rating_count": reviews,
                    "plus_code": plus_code,
                    "website": website
                }
            )

            res = evaluate_candidate(
                target_name=target_info.get("name", title),
                target_lat=target_info.get("lat"),
                target_lng=target_info.get("lng"),
                target_phone=target_phone,
                candidate=cand
            )

            tier = res.match_tier.value
            rule = res.match_rule

            if tier == "strict":
                strict_count += 1
            elif tier == "loose":
                loose_count += 1

            cur_out.execute("""
            INSERT OR REPLACE INTO places (
                cafe_id, canonical_id, title, category_name, searched_name,
                searched_lat, searched_lng, matched_lat, matched_lng, address,
                phone, extra_url, rating, rating_count, place_url,
                match_tier, match_rule, name_score, distance_meters,
                composite_score, phone_matched, rejection_reason,
                extra_json, matched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cid,
                pid,
                title,
                category,
                target_info.get("name"),
                target_info.get("lat"),
                target_info.get("lng"),
                s_lat,
                s_lng,
                address,
                phone,
                website,
                rating,
                reviews,
                g_url,
                tier,
                rule,
                name_score,
                distance,
                res.composite_score,
                1 if phone_matched else 0,
                res.rejection_reason,
                f'{{"cid": "{g_cid}", "fid": "{fid}", "kgmid": "{kgmid}", "plus_code": "{plus_code}"}}',
                scraped_at
            ))

        conn_out.commit()

    # Update checkpoint
    db_manager.update_checkpoint(0)

    # Export to strict and loose DBs
    s_cnt, l_cnt = export_strict_and_loose(
        master_db_path=out_master_db,
        strict_db_path=out_strict_db,
        loose_db_path=out_loose_db
    )

    print("\n" + "=" * 60)
    print("✅ GOOGLE MAPS EXPORT COMPLETE")
    print("=" * 60)
    print(f"Total Google Maps Matches Processed: {len(meta_rows)}")
    print(f"  ⭐ Strict Matches (~99% certainty): {s_cnt} ({s_cnt/len(meta_rows)*100:.1f}%) -> {out_strict_db}")
    print(f"  🔍 Loose Matches (Human Review Queue): {l_cnt} ({l_cnt/len(meta_rows)*100:.1f}%) -> {out_loose_db}")
    print(f"  📞 Phone Number Confirmed Matches: {phone_match_count}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    export_gmaps_dbs()
