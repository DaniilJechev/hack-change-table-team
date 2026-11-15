"""Compatibility alias for buddy.observability.llm_trace."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.observability.llm_trace")
