"""Exceptions raised by the YouTrack API client."""

from __future__ import annotations


class YouTrackError(Exception):
    """Base class for all YouTrack client errors."""


class YouTrackConnectionError(YouTrackError):
    """The server could not be reached or timed out."""


class YouTrackAuthError(YouTrackError):
    """The token was rejected (HTTP 401)."""


class YouTrackPermissionError(YouTrackError):
    """The token is valid but lacks permission (HTTP 403)."""


class YouTrackNotFoundError(YouTrackError):
    """The requested resource does not exist (HTTP 404)."""


class YouTrackRateLimitError(YouTrackError):
    """The server asked the client to slow down (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Store the optional Retry-After hint in seconds."""
        super().__init__(message)
        self.retry_after = retry_after


class YouTrackInvalidQueryError(YouTrackError):
    """The server rejected a search query (HTTP 400, invalid_query)."""


class YouTrackResponseError(YouTrackError):
    """The server answered with something this client cannot interpret."""
