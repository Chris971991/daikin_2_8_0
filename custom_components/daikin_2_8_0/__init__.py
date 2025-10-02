"""The Daikin 2.8.0 integration."""
from __future__ import annotations

import logging
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

# This schema is for backward compatibility with YAML configuration
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
    """Set up the Daikin 2.8.0 component from YAML."""
    if DOMAIN not in config:
        return True

    # Set up entries from YAML (legacy support)
    domain_config = config[DOMAIN]
    hass.data.setdefault(DOMAIN, {})

    # Import YAML configurations as config entries
    for ip_address in domain_config[CONF_IP_ADDRESS]:
        friendly_name = domain_config.get(CONF_FRIENDLY_NAME, {}).get(ip_address, f"Daikin {ip_address}")
        
        # Import the config
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data={
                    CONF_IP_ADDRESS: ip_address,
                    CONF_FRIENDLY_NAME: friendly_name,
                },
            )
        )
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Daikin 2.8.0 from a config entry."""
    from aiohttp import ClientSession
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    hass.data.setdefault(DOMAIN, {})

    ip_address = entry.data[CONF_IP_ADDRESS]
    friendly_name = entry.data.get(CONF_FRIENDLY_NAME, f"Daikin {ip_address}")

    # Create the climate entity with shared aiohttp session
    from .climate import DaikinClimate

    session = async_get_clientsession(hass)
    climate_entity = DaikinClimate(ip_address, friendly_name, session)

    # Combine MAC fetch and initial update into single call - PERFORMANCE OPTIMIZATION
    await climate_entity.initialize_unique_id()
    await climate_entity.async_update()

    # Create and store the coordinator (no need for first refresh, already updated)
    coordinator = DaikinDataUpdateCoordinator(hass, climate_entity)

    # Store both the climate entity and coordinator in hass.data
    hass.data[DOMAIN][ip_address] = {
        "climate": climate_entity,
        "coordinator": coordinator,
        "entry_id": entry.entry_id,
    }

    # Set up all the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Update the entry with the MAC address for true uniqueness
    if entry.unique_id != climate_entity._mac:
        hass.config_entries.async_update_entry(entry, unique_id=climate_entity._mac)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]
    
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if ip_address in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(ip_address)
            
    return unload_ok


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
            await self._climate.async_update()
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