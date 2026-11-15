"""Compatibility alias for buddy.data.contracts."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.data.contracts")
