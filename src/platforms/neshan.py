"""Neshan Maps (neshan.org) high-performance async REST client."""

import asyncio
import base64
import json
import random
import secrets
import time
from typing import List, Optional, Dict, Any
import urllib.parse

import httpx
from loguru import logger

from src.config import PlatformConfig, NESHAN_CONFIG
from src.db import CafeRecord
from src.matcher import MatchCandidate
from src.normalizer import clean_tokens
from src.platforms.base import BasePlatformClient

FILLER = 28


def unwrap_neshan(s: str, filler: int = FILLER) -> Any:
    """Decodes Neshan pwa-api obfuscated response payload."""
    s = s.strip()
    if not s:
        return {}
    if s[0] in "{[":
        return json.loads(s)
    inner = s[filler : len(s) - filler] if filler else s
    at = inner.rfind("@")
    if at < 0 or not inner[at + 1 :].isdigit():
        raise ValueError("no @N marker in response payload")
    cut, body = int(inner[at + 1 :]), inner[:at]
    raw = body[cut:] + body[:cut]
    return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))


def generate_visitor_id() -> str:
    """Generates a UUIDv7-style visitor ID matching Neshan's web client."""
    ms = int(time.time() * 1000).to_bytes(6, "big")
    b = bytearray(ms + secrets.token_bytes(10))
    b[6] = 0x70 | (b[6] & 0x0F)
    b[8] = 0x80 | (b[8] & 0x3F)
    h = b.hex()
    return f"web_{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


class NeshanClient(BasePlatformClient):
    BASE_API = "https://neshan.org/maps/pwa-api"

    def __init__(self, config: PlatformConfig = NESHAN_CONFIG):
        self.config = config
        self.visitor_id = generate_visitor_id()
        self.client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(config.concurrency_limit)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
                    "X-Client-Version": "2038",
                }
            )
        return self.client

    async def _safe_delay(self) -> None:
        delay = random.uniform(self.config.min_delay_seconds, self.config.max_delay_seconds)
        await asyncio.sleep(delay)

    async def search_candidates(self, cafe: CafeRecord) -> List[MatchCandidate]:
        """Searches Neshan map for candidates matching cafe name and near cafe coordinates."""
        raw_query = cafe.name.strip()
        cleaned = " ".join(clean_tokens(raw_query))

        queries = [raw_query]
        if cleaned and cleaned != raw_query:
            queries.append(cleaned)

        all_candidates = []
        seen_ids = set()

        for q in queries:
            cands = await self._execute_search(q, cafe.latitude, cafe.longitude)
            for c in cands:
                if c.candidate_id not in seen_ids:
                    seen_ids.add(c.candidate_id)
                    all_candidates.append(c)
            # If we found at least one close candidate (< 400m), stop searching
            if any(c.latitude and c.longitude and (abs(c.latitude - (cafe.latitude or 0)) < 0.004 and abs(c.longitude - (cafe.longitude or 0)) < 0.004) for c in all_candidates):
                break

        return all_candidates

    async def _execute_search(self, query: str, lat: Optional[float], lng: Optional[float]) -> List[MatchCandidate]:
        client = await self._ensure_client()

        body_data = {
            "uuid": self.visitor_id,
            "term": query,
            "zoom": 15,
            "limit": 15,
            "filters": {},
            "night": False,
            "location": None,
            "center": {"x": lng, "y": lat} if (lat and lng) else None,
            "boundary": None,
        }

        json_str = json.dumps(body_data, ensure_ascii=False, separators=(",", ":"))
        encoded_body = base64.b64encode(urllib.parse.quote(json_str, safe="-_.!~*'()").encode("utf-8")).decode("ascii")

        url = f"{self.BASE_API}/neshan-search"
        params = {"body": encoded_body, "search-in-bound": "false"}
        headers = {"Referer": f"https://neshan.org/maps/search/{urllib.parse.quote(query)}"}

        async with self._semaphore:
            await self._safe_delay()
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 200:
                        data = unwrap_neshan(resp.text)
                        items = data.get("items", []) if isinstance(data, dict) else []
                        return self._parse_items(items)
                    elif resp.status_code in (429, 503):
                        logger.warning(f"Neshan HTTP {resp.status_code} rate limited on '{query}'. Backing off {self.config.retry_delay_seconds * attempt}s...")
                        await asyncio.sleep(self.config.retry_delay_seconds * attempt)
                    else:
                        logger.debug(f"Neshan returned status {resp.status_code} for '{query}'")
                        return []
                except Exception as e:
                    if attempt == self.config.max_retries:
                        logger.error(f"Neshan request failed after {attempt} attempts for '{query}': {e}")
                        return []
                    await asyncio.sleep(self.config.retry_delay_seconds)
        return []

    def _parse_items(self, items: List[Dict[str, Any]]) -> List[MatchCandidate]:
        candidates = []
        for it in items:
            if not isinstance(it, dict) or it.get("isAd"):
                continue

            crowd_id = it.get("crowdId")
            title = (it.get("title") or "").strip()
            loc = it.get("location") or {}
            c_lat = loc.get("y")
            c_lng = loc.get("x")
            subtitle = it.get("subtitle")
            cat_name = it.get("typeTitle")

            # Extract phone from actions if available
            phone = None
            for a in it.get("actions") or []:
                if a.get("type") == "CALL":
                    try:
                        phone = json.loads(a.get("metaData") or "{}").get("phone")
                    except Exception:
                        pass

            stars = it.get("rateStars") or {}
            rating = stars.get("rate")
            votes = stars.get("vote")

            cand = MatchCandidate(
                candidate_id=crowd_id or f"neshan_{c_lat}_{c_lng}",
                title=title,
                latitude=round(c_lat, 6) if c_lat is not None else None,
                longitude=round(c_lng, 6) if c_lng is not None else None,
                address=subtitle,
                phone=phone,
                category=cat_name,
                url=f"https://neshan.org/maps/places/{crowd_id}" if crowd_id else None,
                raw_data={
                    "rating": rating,
                    "votes_count": votes,
                    "type_title": cat_name,
                    "status": (it.get("status") or {}).get("description")
                }
            )
            candidates.append(cand)
        return candidates

    async def enrich_candidate(self, candidate: MatchCandidate) -> MatchCandidate:
        """Enriches candidate with full address, telephone, and social links from info/v2 endpoint."""
        if not candidate.candidate_id or candidate.candidate_id.startswith("neshan_"):
            return candidate

        client = await self._ensure_client()
        url = f"{self.BASE_API}/info/v2/{candidate.candidate_id}"
        headers = {"Referer": candidate.url or f"https://neshan.org/maps/places/{candidate.candidate_id}"}

        async with self._semaphore:
            await self._safe_delay()
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = unwrap_neshan(resp.text)
                    brief = data.get("briefContent") or {}
                    actions = brief.get("actions") or []

                    if brief.get("address"):
                        candidate.address = brief["address"]
                    if brief.get("layerTitle"):
                        candidate.category = brief["layerTitle"]

                    for act in actions:
                        if act.get("handler") == "dial" and act.get("action"):
                            raw_phone = act["action"].replace("tel://", "").replace("tel:", "")
                            if raw_phone:
                                candidate.phone = raw_phone
                        elif act.get("title") == "اینستاگرام" and act.get("action"):
                            candidate.raw_data["instagram"] = act["action"]

                    stars = data.get("rateStars") or {}
                    if stars.get("rate"):
                        candidate.raw_data["rating"] = stars.get("rate")
            except Exception as e:
                logger.debug(f"Failed to enrich Neshan place {candidate.candidate_id}: {e}")

        return candidate

    async def close(self) -> None:
        if self.client and not self.client.is_closed:
            await self.client.aclose()
