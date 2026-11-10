"""Abstract base class for map platform search clients."""

from abc import ABC, abstractmethod
from typing import List, Optional

from src.db import CafeRecord
from src.matcher import MatchCandidate


class BasePlatformClient(ABC):
    @abstractmethod
    async def search_candidates(self, cafe: CafeRecord) -> List[MatchCandidate]:
        """Searches the map platform for candidates around the cafe's location."""
        pass

    @abstractmethod
    async def enrich_candidate(self, candidate: MatchCandidate) -> MatchCandidate:
        """Enriches a candidate with detailed place information (phone, address, etc.) if available."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Closes client connections and frees resources."""
        pass
