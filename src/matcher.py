"""Multi-tiered matching engine: Strict (~99% confidence) vs. Loose (human review queue)."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
import math
import re
from typing import Optional, List, Dict, Any, Tuple

from src.normalizer import clean_tokens, to_phonetic_latin, normalize_phone, normalize_text


class MatchTier(str, Enum):
    STRICT = "strict"    # 98-99%+ confidence match (auto-accepted)
    LOOSE = "loose"      # Borderline candidate (queued for human verification)
    REJECTED = "none"    # Does not meet threshold (not found)


@dataclass
class MatchCandidate:
    candidate_id: str
    title: str
    latitude: Optional[float]
    longitude: Optional[float]
    address: Optional[str] = None
    phone: Optional[str] = None
    category: Optional[str] = None
    url: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = field(default_factory=dict)


@dataclass
class MatchResult:
    is_match: bool
    match_tier: MatchTier
    match_rule: str
    composite_score: float
    name_score: float
    distance_meters: float
    phone_matched: bool
    candidate: Optional[MatchCandidate] = None
    rejection_reason: Optional[str] = None


def compute_name_similarity(name1: str, name2: str) -> float:
    """Computes similarity between two venue names using direct, joined, phonetic, and guarded token matching."""
    if not name1 or not name2:
        return 0.0

    tok1 = clean_tokens(name1)
    tok2 = clean_tokens(name2)

    s1, s2 = ' '.join(tok1), ' '.join(tok2)
    direct_ratio = SequenceMatcher(None, s1, s2).ratio()

    joined_s1, joined_s2 = ''.join(tok1), ''.join(tok2)
    joined_ratio = SequenceMatcher(None, joined_s1, joined_s2).ratio()

    lat1, lat2 = to_phonetic_latin(tok1), to_phonetic_latin(tok2)
    lat_s1, lat_s2 = ''.join(lat1), ''.join(lat2)
    phonetic_ratio = SequenceMatcher(None, lat_s1, lat_s2).ratio() if (lat_s1 and lat_s2) else 0.0

    # Guarded token containment (only for tokens >= 3 chars to prevent false single-letter positives)
    set1 = {t for t in lat1 if len(t) >= 3}
    set2 = {t for t in lat2 if len(t) >= 3}
    if set1 and set2:
        overlap = len(set1 & set2) / min(len(set1), len(set2))
        containment = 0.85 if (set1.issubset(set2) or set2.issubset(set1)) else (overlap * 0.8)
    else:
        containment = 0.0

    return round(max(direct_ratio, joined_ratio, phonetic_ratio, containment), 3)


def haversine_distance(lat1: Optional[float], lon1: Optional[float], lat2: Optional[float], lon2: Optional[float]) -> float:
    """Calculates haversine distance in meters with full coordinate sanity validation."""
    if None in (lat1, lon1, lat2, lon2):
        return float('inf')
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
            return float('inf')
        if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
            return float('inf')
        if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
            return float('inf')
    except (ValueError, TypeError):
        return float('inf')

    R = 6371000.0  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(R * c, 1)


def check_phone_match(phone1: Optional[str], phone2: Optional[str]) -> bool:
    """Checks if two phone numbers match on their trailing significant digits."""
    p1 = normalize_phone(phone1 or "")
    p2 = normalize_phone(phone2 or "")
    if len(p1) >= 7 and len(p2) >= 7:
        return (p1 == p2) or p1.endswith(p2[-7:]) or p2.endswith(p1[-7:])
    return False


def evaluate_candidate(
    target_name: str,
    target_lat: Optional[float],
    target_lng: Optional[float],
    target_phone: Optional[str],
    candidate: MatchCandidate
) -> MatchResult:
    """Evaluates a single candidate against the target venue and classifies into STRICT, LOOSE, or REJECTED."""
    name_score = compute_name_similarity(target_name, candidate.title)
    dist = haversine_distance(target_lat, target_lng, candidate.latitude, candidate.longitude)
    phone_matched = check_phone_match(target_phone, candidate.phone)

    # Proximity decay score (1.0 at <=30m decaying to 0.0 at 1000m)
    if dist <= 30.0:
        dist_score = 1.0
    elif dist < 1000.0:
        dist_score = max(0.0, 1.0 - (dist - 30.0) / 970.0)
    else:
        dist_score = 0.0

    # Composite score: 65% name similarity + 35% proximity (+ bonus for phone match)
    composite = (0.65 * name_score) + (0.35 * dist_score)
    if phone_matched:
        composite = min(1.0, composite + 0.15)
    composite = round(composite, 3)

    # --- TIER 1: STRICT MATCH CRITERIA (98-99%+ Confidence) ---
    # Rule 1.1: Verified Phone Match + Name consistency within 2.5km (handles GPS inaccuracies)
    if phone_matched and name_score >= 0.45 and dist <= 2500.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.STRICT,
            match_rule="STRICT_PHONE_AND_NAME_MATCH",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=True,
            candidate=candidate
        )

    # Rule 1.2: High name similarity (>= 0.85) in close proximity (<= 150m)
    if name_score >= 0.85 and dist <= 150.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.STRICT,
            match_rule="STRICT_HIGH_SIMILARITY_CLOSE_PROXIMITY",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=phone_matched,
            candidate=candidate
        )

    # Rule 1.3: Near-exact name match (>= 0.95) within 350m
    if name_score >= 0.95 and dist <= 350.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.STRICT,
            match_rule="STRICT_EXACT_NAME_MODERATE_RADIUS",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=phone_matched,
            candidate=candidate
        )

    # --- TIER 2: LOOSE MATCH CRITERIA (Candidate Queue for Human Review) ---
    # Rule 2.1: Moderate name similarity (>= 0.60) within 500m
    if name_score >= 0.60 and dist <= 500.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.LOOSE,
            match_rule="LOOSE_MODERATE_SIMILARITY_NEARBY",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=phone_matched,
            candidate=candidate
        )

    # Rule 2.2: High name similarity (>= 0.80) in extended radius (up to 1500m, handles city coordinate shifts)
    if name_score >= 0.80 and dist <= 1500.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.LOOSE,
            match_rule="LOOSE_HIGH_SIMILARITY_EXTENDED_RADIUS",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=phone_matched,
            candidate=candidate
        )

    # Rule 2.3: Lower name score (>= 0.50) but extremely close physically (<= 80m, e.g. renamed venues or transliterated names)
    if name_score >= 0.50 and dist <= 80.0:
        return MatchResult(
            is_match=True,
            match_tier=MatchTier.LOOSE,
            match_rule="LOOSE_CLOSE_PROXIMITY_PARTIAL_NAME",
            composite_score=composite,
            name_score=name_score,
            distance_meters=dist,
            phone_matched=phone_matched,
            candidate=candidate
        )

    # --- REJECTED ---
    rejection = []
    if dist > 1500.0:
        rejection.append(f"Distance too far ({dist}m > 1500m)")
    if name_score < 0.50:
        rejection.append(f"Name similarity too low ({name_score:.2f} < 0.50)")
    reason = " & ".join(rejection) if rejection else f"Composite score ({composite}) below thresholds"

    return MatchResult(
        is_match=False,
        match_tier=MatchTier.REJECTED,
        match_rule="REJECTED",
        composite_score=composite,
        name_score=name_score,
        distance_meters=dist,
        phone_matched=phone_matched,
        candidate=candidate,
        rejection_reason=reason
    )


def rank_and_select_best_match(
    target_name: str,
    target_lat: Optional[float],
    target_lng: Optional[float],
    target_phone: Optional[str],
    candidates: List[MatchCandidate]
) -> MatchResult:
    """Evaluates all candidates and returns the best match (Strict prioritized over Loose, highest score first)."""
    if not candidates:
        return MatchResult(
            is_match=False,
            match_tier=MatchTier.REJECTED,
            match_rule="NO_CANDIDATES",
            composite_score=0.0,
            name_score=0.0,
            distance_meters=float('inf'),
            phone_matched=False,
            rejection_reason="No candidates returned from search"
        )

    evaluated = [
        evaluate_candidate(target_name, target_lat, target_lng, target_phone, c)
        for c in candidates
    ]

    # Priority 1: Strict matches
    stricts = [r for r in evaluated if r.match_tier == MatchTier.STRICT]
    if stricts:
        stricts.sort(key=lambda r: (r.phone_matched, r.composite_score, -r.distance_meters), reverse=True)
        return stricts[0]

    # Priority 2: Loose matches
    looses = [r for r in evaluated if r.match_tier == MatchTier.LOOSE]
    if looses:
        looses.sort(key=lambda r: (r.composite_score, -r.distance_meters), reverse=True)
        return looses[0]

    # Otherwise return best rejected candidate for diagnostic logging
    evaluated.sort(key=lambda r: (r.composite_score, -r.distance_meters), reverse=True)
    return evaluated[0]
