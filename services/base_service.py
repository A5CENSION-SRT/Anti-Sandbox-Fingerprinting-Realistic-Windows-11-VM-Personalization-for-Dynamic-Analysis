"""Abstract base class for all services."""

from abc import ABC, abstractmethod


class BaseService(ABC):
    """Base class that all services must inherit from."""

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Return the unique name of this service."""

    @abstractmethod
    def apply(self, context: dict) -> None:
        """Execute the service's primary operation."""
