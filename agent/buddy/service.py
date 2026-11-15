"""Compatibility alias for buddy.orchestration.service."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.orchestration.service")
