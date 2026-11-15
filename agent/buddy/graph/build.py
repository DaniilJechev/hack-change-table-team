"""Compatibility alias for buddy.orchestration.graph."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.orchestration.graph")
