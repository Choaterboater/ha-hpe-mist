"""Errors any presence source raises, so the coordinator does not depend on one controller."""


class SourceError(Exception):
    """The controller could not be reached or answered with an error."""


class SourceAuthError(SourceError):
    """The credentials were refused."""


class SourceBusyError(SourceError):
    """The controller asked us to slow down (429) or had a server error (5xx)."""

    retry_after: float | None = None
