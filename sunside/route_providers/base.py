from abc import ABC, abstractmethod
from datetime import datetime

from sunside.models import RoutePoint


class RouteProvider(ABC):
    """
    Base class for all route providers.
    Each provider geocodes/fetches the actual route geometry and returns
    a list of RoutePoints with coordinates and timestamps.
    """

    @abstractmethod
    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
    ) -> list[RoutePoint]:
        """
        Fetch route from origin to destination departing at `departure`.
        Returns points ordered from start to end, with interpolated timestamps.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name shown in the UI."""
        ...
