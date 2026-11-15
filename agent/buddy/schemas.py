"""Compatibility alias for buddy.contracts."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.contracts")
