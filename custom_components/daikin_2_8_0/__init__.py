"""The Daikin 2.8.0 integration."""
from __future__ import annotations

import logging
import asyncio
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_FRIENDLY_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

DOMAIN = "daikin_2_8_0"
UPDATE_INTERVAL = timedelta(seconds=60)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_IP_ADDRESS): vol.All(
                    cv.ensure_list, [cv.string]
                ),
                vol.Optional(CONF_FRIENDLY_NAME): vol.Schema({cv.string: cv.string}),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Daikin 2.8.0 component."""
    if DOMAIN not in config:
        return True

    domain_config = config[DOMAIN]
    hass.data.setdefault(DOMAIN, {})

    # Set up the climate entity first
    for ip_address in domain_config[CONF_IP_ADDRESS]:
        friendly_name = domain_config.get(CONF_FRIENDLY_NAME, {}).get(ip_address, f"Daikin {ip_address}")
        
        # Pass the discovery info to the climate platform
        hass.async_create_task(
            hass.helpers.discovery.async_load_platform(
                Platform.CLIMATE, 
                DOMAIN, 
                {"ip_address": ip_address, "friendly_name": friendly_name},
                config
            )
        )
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Daikin 2.8.0 from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Forward the setup to the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class DaikinDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Daikin data."""

    def __init__(self, hass: HomeAssistant, climate_entity) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._climate = climate_entity

    async def _async_update_data(self):
        """Fetch data from Daikin AC."""
        try:
            await self.hass.async_add_executor_job(self._climate.update)
            return {
                "current_temperature": self._climate.current_temperature,
                "outside_temperature": self._climate._outside_temperature,
                "current_humidity": self._climate._current_humidity,
                "hvac_mode": self._climate.hvac_mode,
                "fan_mode": self._climate.fan_mode,
                "swing_mode": self._climate.swing_mode,
                "energy_today": self._climate._energy_today,
                "runtime_today": self._climate._runtime_today,
                "target_temperature": self._climate.target_temperature,
            }
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Daikin AC: {err}")