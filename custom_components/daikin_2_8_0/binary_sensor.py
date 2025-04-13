"""Binary sensor platform for Daikin 2.8.0 integration."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_TYPES = {
    "is_running": {
        "name": "Running",
        "device_class": BinarySensorDeviceClass.RUNNING,
        "icon": "mdi:air-conditioner",
        "condition": lambda climate: climate.hvac_mode != HVACMode.OFF,
    },
    "is_cooling": {
        "name": "Cooling",
        "device_class": BinarySensorDeviceClass.COLD,
        "icon": "mdi:snowflake",
        "condition": lambda climate: climate.hvac_mode == HVACMode.COOL,
    },
    "is_heating": {
        "name": "Heating",
        "device_class": BinarySensorDeviceClass.HEAT,
        "icon": "mdi:fire",
        "condition": lambda climate: climate.hvac_mode == HVACMode.HEAT,
    },
}


async def async_setup_platform(
    hass: HomeAssistant, 
    config: Dict[str, Any], 
    async_add_entities: AddEntitiesCallback, 
    discovery_info=None
) -> None:
    """Set up Daikin 2.8.0 binary sensor entities."""
    if discovery_info is None:
        return

    ip_address = discovery_info.get("ip_address")
    
    if ip_address not in hass.data[DOMAIN]:
        _LOGGER.error(f"No coordinator for IP address {ip_address}")
        return
        
    climate_entity = hass.data[DOMAIN][ip_address]["climate"]
    
    entities = []
    
    for sensor_type, details in BINARY_SENSOR_TYPES.items():
        entities.append(
            DaikinBinarySensor(
                climate_entity=climate_entity,
                sensor_type=sensor_type,
                details=details,
            )
        )
    
    async_add_entities(entities)


class DaikinBinarySensor(BinarySensorEntity):
    """Representation of a Daikin binary sensor."""

    def __init__(
        self,
        climate_entity,
        sensor_type: str,
        details: dict,
    ) -> None:
        """Initialize the binary sensor."""
        self._climate = climate_entity
        self._sensor_type = sensor_type
        self._condition = details["condition"]
        self._attr_name = f"{self._climate._friendly_name} {details['name']}"
        self._attr_unique_id = f"{self._climate._mac}_{sensor_type}"
        self._attr_device_class = details.get("device_class")
        self._attr_icon = details.get("icon")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Daikin AC."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._climate._mac)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self._condition(self._climate)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._climate is not None

    async def async_update(self) -> None:
        """Get the latest data from the binary sensor."""
        # The climate entity already handles updates
        pass