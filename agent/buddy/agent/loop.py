"""Compatibility alias for buddy.orchestration.agent_loop."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("buddy.orchestration.agent_loop")
