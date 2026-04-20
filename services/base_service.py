"""Abstract base class for all services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.service_context import ServiceContext


class BaseService(ABC):
    """Base class that all services must inherit from."""

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Return the unique name of this service."""

    @abstractmethod
    def apply(self, ctx: "ServiceContext") -> None:
        """Execute the service's primary operation."""
