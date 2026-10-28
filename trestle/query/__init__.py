"""Query layer — named views over evidence."""

from trestle.query.backend import QueryBackend
from trestle.query.fs import FilesystemQueryBackend

__all__ = ["FilesystemQueryBackend", "QueryBackend"]
