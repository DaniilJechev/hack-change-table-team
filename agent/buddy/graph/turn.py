"""Compatibility alias for buddy.observability.turn_trace."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.observability.turn_trace")
