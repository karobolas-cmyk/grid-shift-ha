"""Load the pure optimizer modules without requiring Home Assistant in CI."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[1]

for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.gridshift_battery", ROOT / "custom_components/gridshift_battery"),
):
    module = ModuleType(name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules.setdefault(name, module)

# The controller tests exercise ordering without installing Home Assistant.
homeassistant = ModuleType("homeassistant")
homeassistant_core = ModuleType("homeassistant.core")
homeassistant_exceptions = ModuleType("homeassistant.exceptions")
homeassistant_storage = ModuleType("homeassistant.helpers.storage")
homeassistant_helpers = ModuleType("homeassistant.helpers")


class HomeAssistant:
    pass


class Store:
    pass


class HomeAssistantError(Exception):
    pass


homeassistant_core.HomeAssistant = HomeAssistant
homeassistant_storage.Store = Store
homeassistant_exceptions.HomeAssistantError = HomeAssistantError
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.core", homeassistant_core)
sys.modules.setdefault("homeassistant.exceptions", homeassistant_exceptions)
sys.modules.setdefault("homeassistant.helpers", homeassistant_helpers)
sys.modules.setdefault("homeassistant.helpers.storage", homeassistant_storage)
