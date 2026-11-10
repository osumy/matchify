"""Balad Maps (balad.ir / raah.ir) high-performance async REST client."""

import asyncio
import math
import random
from typing import List, Optional, Dict, Any
import uuid

import httpx
from loguru import logger

from src.config import PlatformConfig, BALAD_CONFIG
from src.db import CafeRecord
from src.matcher import MatchCandidate
from src.platforms.base import BasePlatformClient


def build_bounding_polygon(lat: float, lng: float, radius_km: float = 5.0) -> str:
    """Builds a closed lng,lat polygon ring around coordinates for Balad search."""
    dlat = radius_km / 111.32
    dlng = radius_km / (111.32 * math.cos(math.radians(lat)))
    n, s, w, e = lat + dlat, lat - dlat, lng - dlng, lng + dlng
    ring = [(w, n), (e, n), (e, s), (w, s), (w, n)]
    return "|".join(f"{x:.6f},{y:.6f}" for x, y in ring)


class BaladClient(BasePlatformClient):
    SEARCH_API = "https://search.raah.ir/v6/"
    POI_API = "https://poi.raah.ir/web/v4"

    def __init__(self, config: PlatformConfig = BALAD_CONFIG):
        self.config = config
        self.device_id = str(uuid.uuid4())
        self.client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(config.concurrency_limit)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
                    "Origin": "https://balad.ir",
                    "Referer": "https://balad.ir/",
                    "platform": "web",
                    "device-id": self.device_id,
                }
            )
        return self.client

    async def _safe_delay(self) -> None:
        delay = random.uniform(self.config.min_delay_seconds, self.config.max_delay_seconds)
        await asyncio.sleep(delay)

    async def search_candidates(self, cafe: CafeRecord) -> List[MatchCandidate]:
        """Searches Balad map for candidates matching cafe name and near cafe coordinates."""
        raw_query = cafe.name.strip()
        from src.normalizer import clean_tokens
        cleaned = " ".join(clean_tokens(raw_query))

        queries = [cleaned] if cleaned and cleaned != raw_query else [raw_query]
        if raw_query not in queries:
            queries.append(raw_query)

        all_candidates = []
        seen_ids = set()

        for q in queries:
            cands = await self._execute_search(q, cafe.latitude, cafe.longitude)
            for c in cands:
                if c.candidate_id not in seen_ids:
                    seen_ids.add(c.candidate_id)
                    all_candidates.append(c)
            # If we found at least one close candidate (< 500m), we don't need additional query variations
            if any(c.latitude and c.longitude and math.hypot(c.latitude - (cafe.latitude or 0), c.longitude - (cafe.longitude or 0)) < 0.005 for c in all_candidates):
                break

        return all_candidates

    async def _execute_search(self, query: str, lat: Optional[float], lng: Optional[float]) -> List[MatchCandidate]:
        client = await self._ensure_client()
        lat = lat or 35.6995
        lng = lng or 51.4119

        polygon = build_bounding_polygon(lat, lng, radius_km=5.0)

        params = {
            "text": query,
            "polygon": polygon,
            "zoom": "13",
            "camera": f"{lng:.6f},{lat:.6f}"
        }

        headers = {
            "app-session": str(uuid.uuid4()),
            "search-session": str(uuid.uuid4()),
        }

        async with self._semaphore:
            await self._safe_delay()
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    resp = await client.get(self.SEARCH_API, params=params, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("results", []) if isinstance(data, dict) else []
                        return self._parse_results(results)
                    elif resp.status_code in (429, 503):
                        logger.warning(f"Balad HTTP {resp.status_code} rate limited on '{query}'. Backing off {self.config.retry_delay_seconds * attempt}s...")
                        await asyncio.sleep(self.config.retry_delay_seconds * attempt)
                    else:
                        logger.debug(f"Balad returned status {resp.status_code} for '{query}'")
                        return []
                except Exception as e:
                    if attempt == self.config.max_retries:
                        logger.error(f"Balad request failed after {attempt} attempts for '{query}': {e}")
                        return []
                    await asyncio.sleep(self.config.retry_delay_seconds)
        return []

    def _parse_results(self, results: List[Dict[str, Any]]) -> List[MatchCandidate]:
        candidates = []
        for r in results:
            if not isinstance(r, dict):
                continue
            r_type = r.get("type")
            # We want POI places
            if r_type not in ("poi", "bundle-poi", "compound-poi", None):
                continue

            title = (r.get("title") or r.get("maintext") or "").strip()
            if not title:
                continue

            token = r.get("poi_token") or r.get("id")
            pt = r.get("center_point") or r.get("location")
            c_lat, c_lng = None, None
            if isinstance(pt, list) and len(pt) == 2:
                # GeoJSON coordinates order: [lng, lat]
                c_lng = round(float(pt[0]), 6)
                c_lat = round(float(pt[1]), 6)

            addr = r.get("address") or r.get("subtext")

            cand = MatchCandidate(
                candidate_id=token or f"balad_{c_lat}_{c_lng}",
                title=title,
                latitude=c_lat,
                longitude=c_lng,
                address=addr,
                phone=None,
                category=r.get("category"),
                url=f"https://balad.ir/p/{token}" if token else None,
                raw_data={"poi_token": token, "type": r_type}
            )
            candidates.append(cand)
        return candidates

    async def enrich_candidate(self, candidate: MatchCandidate) -> MatchCandidate:
        """Fetches detailed POI metadata (phone, exact address, instagram, rating) from Balad."""
        token = candidate.candidate_id
        if not token or token.startswith("balad_"):
            return candidate

        client = await self._ensure_client()
        url = f"{self.POI_API}/{token}"

        async with self._semaphore:
            await self._safe_delay()
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()

                    if data.get("category"):
                        candidate.category = data["category"]

                    rating_info = data.get("rating") or {}
                    if isinstance(rating_info, dict):
                        candidate.raw_data["rating"] = rating_info.get("score")
                        candidate.raw_data["reviews_count"] = rating_info.get("count")

                    # Extract phone link
                    phone_link = data.get("phone_link")
                    if phone_link:
                        raw_p = phone_link.replace("tel://", "").replace("tel:", "").strip()
                        if raw_p:
                            candidate.phone = raw_p

                    # Parse fields array
                    for f in data.get("fields") or []:
                        f_type = f.get("type")
                        f_icon = f.get("icon")
                        f_val = f.get("value")

                        if f_icon == "gps" and f_val:
                            candidate.address = f_val
                        elif f_icon == "phone" and f.get("text"):
                            if not candidate.phone:
                                candidate.phone = f["text"].strip()
                        elif f_icon == "instagram" and f_val:
                            candidate.raw_data["instagram"] = f_val

                    # If schema address is present
                    seo = data.get("seo_details") or {}
                    schemas = seo.get("schemas") or []
                    for s in schemas:
                        addr_obj = s.get("address") or {}
                        if addr_obj.get("telephone") and not candidate.phone:
                            candidate.phone = addr_obj["telephone"]

            except Exception as e:
                logger.debug(f"Failed to enrich Balad place {token}: {e}")

        return candidate

    async def close(self) -> None:
        if self.client and not self.client.is_closed:
            await self.client.aclose()
