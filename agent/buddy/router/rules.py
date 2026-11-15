"""Compatibility alias for buddy.orchestration.router.rules."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.orchestration.router.rules")
