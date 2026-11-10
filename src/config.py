"""Configuration parameters for Matchify scrapers, rate limits, and output schemas."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlatformConfig:
    platform_name: str
    min_delay_seconds: float = 0.35
    max_delay_seconds: float = 0.75
    concurrency_limit: int = 2
    timeout_seconds: float = 12.0
    max_retries: int = 3
    retry_delay_seconds: float = 2.0
    enrich_places: bool = True  # whether to make secondary call for complete address/phone details


# Platform-specific default configurations
NESHAN_CONFIG = PlatformConfig(
    platform_name="neshan",
    min_delay_seconds=0.30,
    max_delay_seconds=0.60,
    concurrency_limit=3,
    timeout_seconds=10.0,
    enrich_places=True
)

BALAD_CONFIG = PlatformConfig(
    platform_name="balad",
    min_delay_seconds=0.35,
    max_delay_seconds=0.70,
    concurrency_limit=3,
    timeout_seconds=10.0,
    enrich_places=True
)

GMAPS_CONFIG = PlatformConfig(
    platform_name="gmaps",
    min_delay_seconds=4.5,
    max_delay_seconds=8.5,
    concurrency_limit=1,
    timeout_seconds=20.0,
    enrich_places=True
)
