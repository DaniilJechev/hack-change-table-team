"""Our team data: offline seed bundle (demo/tests) or the Go backend (buddy-mcp reads, REST writes)."""

from buddy.data.backend import BackendData
from buddy.data.base import DataError, TeamData
from buddy.data.seed import SeedData, load_bundle

__all__ = ["BackendData", "DataError", "SeedData", "TeamData", "load_bundle"]
