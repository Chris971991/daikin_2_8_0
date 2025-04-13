"""Sensor platform for Daikin 2.8.0 integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
    CONF_IP_ADDRESS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    "temperature": {
        "name": "Temperature",
        "key": "current_temperature",
        "icon": "mdi:thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfTemperature.CELSIUS,
    },
    "outside_temperature": {
        "name": "Outside Temperature",
        "key": "outside_temperature",
        "icon": "mdi:thermometer-lines",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": UnitOfTemperature.CELSIUS,
    },
    "humidity": {
        "name": "Humidity",
        "key": "current_humidity",
        "icon": "mdi:water-percent",
        "device_class": SensorDeviceClass.HUMIDITY,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
    },
    "energy_today": {
        "name": "Energy Today",
        "key": "energy_today",
        "icon": "mdi:flash",
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "unit": UnitOfEnergy.KILO_WATT_HOUR,
    },
    "runtime_today": {
        "name": "Runtime Today",
        "key": "runtime_today",
        "icon": "mdi:timer-outline",
        "device_class": SensorDeviceClass.DURATION,
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "unit": UnitOfTime.MINUTES,
    },
    "hvac_mode": {
        "name": "HVAC Mode",
        "key": "hvac_mode",
        "icon": "mdi:thermostat",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "fan_mode": {
        "name": "Fan Mode",
        "key": "fan_mode",
        "icon": "mdi:fan",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    "swing_mode": {
        "name": "Swing Mode",
        "key": "swing_mode",
        "icon": "mdi:air-conditioner",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for Daikin 2.8.0 based on config_entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]
    
    if ip_address not in hass.data[DOMAIN]:
        _LOGGER.error(f"No coordinator for IP address {ip_address}")
        return
        
    climate_entity = hass.data[DOMAIN][ip_address]["climate"]
    
    entities = []
    
    for sensor_type, details in SENSOR_TYPES.items():
        entities.append(
            DaikinSensor(
                climate_entity=climate_entity,
                sensor_type=sensor_type,
                details=details,
            )
        )
    
    async_add_entities(entities)
    
    
async def async_setup_platform(
    hass: HomeAssistant, 
    config: Dict[str, Any], 
    async_add_entities: AddEntitiesCallback, 
    discovery_info=None
) -> None:
    """Set up Daikin 2.8.0 sensor entities through YAML."""
    # This is for backward compatibility only
    if discovery_info is None:
        return
        
    _LOGGER.warning("YAML configuration is deprecated, please use the UI configuration")


class DaikinSensor(SensorEntity):
    """Representation of a Daikin sensor."""

    def __init__(
        self,
        climate_entity,
        sensor_type: str,
        details: dict,
    ) -> None:
        """Initialize the sensor."""
        self._climate = climate_entity
        self._sensor_type = sensor_type
        self._attr_name = f"{self._climate._friendly_name} {details['name']}"
        self._attr_unique_id = f"{self._climate._mac}_{sensor_type}"
        self._attr_device_class = details.get("device_class")
        self._attr_state_class = details.get("state_class")
        self._attr_native_unit_of_measurement = details.get("unit")
        self._attr_icon = details.get("icon")
        self._key = details["key"]
        
        if sensor_type in ["hvac_mode", "fan_mode", "swing_mode"]:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Daikin AC."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._climate._mac)},
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check if the attribute exists directly or with a leading underscore
        direct_attr = hasattr(self._climate, self._key)
        underscore_attr = not self._key.startswith('_') and hasattr(self._climate, f"_{self._key}")
        
        # Also check if the value is not None
        if direct_attr:
            direct_value = getattr(self._climate, self._key, None) is not None
        else:
            direct_value = False
            
        if underscore_attr:
            underscore_value = getattr(self._climate, f"_{self._key}", None) is not None
        else:
            underscore_value = False
            
        return (direct_attr and direct_value) or (underscore_attr and underscore_value)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        # First try to access the attribute directly
        value = getattr(self._climate, self._key, None)
        
        # If that fails, try with a leading underscore
        if value is None and not self._key.startswith('_'):
            value = getattr(self._climate, f"_{self._key}", None)
            
        return value

    async def async_update(self) -> None:
        """Get the latest data from the sensor."""
        # The climate entity already handles updates
        pass