"""Daikin 2.8.0 Climate integration for Home Assistant with Smart Temperature Clipping."""
import logging
import aiohttp
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    HVACMode,
    SWING_OFF,
    SWING_BOTH,
    SWING_VERTICAL,
    SWING_HORIZONTAL
)
from homeassistant.const import UnitOfTemperature, CONF_IP_ADDRESS, CONF_FRIENDLY_NAME
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.entity import DeviceInfo
from typing import Any, List, Optional
from dataclasses import dataclass, field
from enum import StrEnum

from . import DOMAIN, DaikinDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class DaikinAttribute:
    name: str
    value: float
    path: list[str]
    to: str

    def format(self) -> str:
        return {"pn": self.name, "pv": self.value}

class HAFanMode(StrEnum):
    FAN_QUIET = "Quiet"
    FAN_AUTO = "Auto"
    FAN_LEVEL1 = "Level 1"
    FAN_LEVEL2 = "Level 2"
    FAN_LEVEL3 = "Level 3"
    FAN_LEVEL4 = "Level 4"
    FAN_LEVEL5 = "Level 5"

TURN_OFF_SWING_AXIS = "000000"
TURN_ON_SWING_AXIS = "0F0000"

# Fan mode values for e_3001
FAN_MODE_MAP = {
    HAFanMode.FAN_AUTO : "0A00",
    HAFanMode.FAN_QUIET : "0B00",
    HAFanMode.FAN_LEVEL1 : "0300",
    HAFanMode.FAN_LEVEL2 : "0400",
    HAFanMode.FAN_LEVEL3 : "0500",
    HAFanMode.FAN_LEVEL4 : "0600",
    HAFanMode.FAN_LEVEL5 : "0700"
}

# Fan mode values for e_3003 (p_2A)
FAN_MODE_MAP_E3003 = {
    HAFanMode.FAN_AUTO : "00",
    HAFanMode.FAN_QUIET : "0B",
    HAFanMode.FAN_LEVEL1 : "03",
    HAFanMode.FAN_LEVEL2 : "04",
    HAFanMode.FAN_LEVEL3 : "05",
    HAFanMode.FAN_LEVEL4 : "06",
    HAFanMode.FAN_LEVEL5 : "07"
}

# Vertical, horizontal
HVAC_MODE_TO_SWING_ATTR_NAMES = {
    HVACMode.AUTO : ("p_05", "p_06"),  # Changed from p_20/p_21 to p_05/p_06 based on your device's response
    HVACMode.COOL : ("p_05", "p_06"),
    HVACMode.HEAT : ("p_07", "p_08"),
    HVACMode.FAN_ONLY : ("p_05", "p_06"),  # Changed from p_24/p_25 to p_05/p_06
    HVACMode.DRY : ("p_05", "p_06")  # Changed from p_22/p_23 to p_05/p_06
}

HVAC_MODE_TO_FAN_SPEED_ATTR_NAME = {
    HVACMode.AUTO : "p_2A",  # This is in e_3003
    HVACMode.COOL : "p_09",  # This is in e_3001
    HVACMode.HEAT : "p_0A",  # This is in e_3001
    HVACMode.FAN_ONLY : "p_28",  # This is in e_3001
    # HVACMode.DRY : "dummy" There is no fan mode for dry. It's always automatic.
}

# Map to indicate which entity contains each fan speed parameter
FAN_SPEED_ENTITY_MAP = {
    "p_2A": "e_3003",  # AUTO mode fan speed is in e_3003
    "p_09": "e_3001",  # COOL mode fan speed is in e_3001
    "p_0A": "e_3001",  # HEAT mode fan speed is in e_3001
    "p_28": "e_3001"   # FAN_ONLY mode fan speed is in e_3001
}

MODE_MAP = {
    "0300" : HVACMode.AUTO,
    "0200" : HVACMode.COOL,
    "0100" : HVACMode.HEAT,
    "0000" : HVACMode.FAN_ONLY,
    "0500" : HVACMode.DRY
}

HVAC_TO_TEMP_HEX = {
    HVACMode.COOL : "p_02",
    HVACMode.HEAT : "p_03",
    HVACMode.AUTO : "p_1D"
}

REVERSE_MODE_MAP = {v: k for k, v in MODE_MAP.items()}
REVERSE_FAN_MODE_MAP = {v: k for k, v in FAN_MODE_MAP.items()}

@dataclass
class DaikinRequest:
    attributes: list[DaikinAttribute] = field(default_factory=list)

    def serialize(self, payload=None) -> dict:
        if payload is None:
            payload = {
                'requests' : []
            }

        def get_existing_index(name: str, children: list) -> int:
            for index, child in enumerate(children):
                if child.get("pn") == name:
                    return index
            return -1

        def get_existing_to(to: str, requests: list) -> any:
            for request in requests:
                this_to = request.get("to")
                if this_to == to:
                    return request

        for attribute in self.attributes:
            to = get_existing_to(attribute.to, payload['requests'])
            if to is None:
                payload['requests'].append({
                    'op': 3,
                    'pc': {
                        "pn": "dgc_status",
                        "pch": []
                    },
                    "to": attribute.to
                })
                to = payload['requests'][-1]
            entry = to['pc']['pch']
            for pn in attribute.path:
                index = get_existing_index(pn, entry)
                if index == -1:
                    entry.append({
                        "pn": pn,
                        "pch": []
                    })
                entry = entry[-1]['pch']
            entry.append(attribute.format())
        return payload

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Daikin climate device from a config entry."""
    ip_address = entry.data[CONF_IP_ADDRESS]

    # The climate entity is already created in __init__.py
    if ip_address in hass.data[DOMAIN]:
        climate_entity = hass.data[DOMAIN][ip_address]["climate"]
        async_add_entities([climate_entity])
    else:
        _LOGGER.error("Climate entity for %s not found", ip_address)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the air conditioner platform through YAML."""
    if discovery_info is None:
        return

    # This is only for backward compatibility and should not be used
    _LOGGER.warning("YAML configuration is deprecated, please use the UI configuration")

class DaikinClimate(ClimateEntity):
    """Representation of a Daikin climate device."""

    def __init__(self, ip_address: str, friendly_name: str, session: aiohttp.ClientSession):
        """Initialize the climate entity."""
        self._ip_address = ip_address
        self._friendly_name = friendly_name
        self._name = f"{friendly_name} Climate"
        self.url = f"http://{ip_address}/dsiot/multireq"
        self._session = session
        self._hvac_mode = HVACMode.OFF
        self._fan_mode = HAFanMode.FAN_QUIET
        self._swing_mode = SWING_OFF
        self._temperature = None
        self._outside_temperature = None
        self._target_temperature = None
        self._current_temperature = None
        self._current_humidity = None
        self._runtime_today = 0
        self._energy_today = 0
        self._mac = None
        self._max_temp = 30
        self._min_temp = 10
        # New: track temperature adjustments for smart clipping
        self._last_temp_adjustment = None

        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO, HVACMode.DRY, HVACMode.FAN_ONLY]
        self._attr_fan_modes = [
            HAFanMode.FAN_QUIET,
            HAFanMode.FAN_AUTO,
            HAFanMode.FAN_LEVEL1,
            HAFanMode.FAN_LEVEL2,
            HAFanMode.FAN_LEVEL3,
            HAFanMode.FAN_LEVEL4,
            HAFanMode.FAN_LEVEL5
        ]
        self._attr_swing_modes = [
            SWING_OFF,
            SWING_BOTH,
            SWING_VERTICAL,
            SWING_HORIZONTAL
        ]
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.SWING_MODE
        self._enable_turn_on_off_backwards_compatibility = False

    async def initialize_unique_id(self):
        """Get the MAC address to use as the unique ID."""
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge.adp_i"}
            ]
        }
        try:
            async with self._session.post(self.url, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                mac_value = self.find_value_by_pn(data, "/dsiot/edge.adp_i", "adp_i", "mac")
                if mac_value is not None:
                    self._mac = format_mac(mac_value)
                    _LOGGER.info(f"Initialized Daikin AC with MAC: {self._mac}")
                else:
                    self._mac = f"daikin_{self._ip_address.replace('.', '_')}"
                    _LOGGER.warning(f"Could not get MAC address, using fallback ID: {self._mac}")
        except Exception as e:
            self._mac = f"daikin_{self._ip_address.replace('.', '_')}"
            _LOGGER.error(f"Error getting MAC address: {e}. Using fallback ID: {self._mac}")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this Daikin AC."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=self._friendly_name,
            manufacturer="Daikin",
            model="Daikin Air Conditioner",
            sw_version="2.8.0",
            configuration_url=f"http://{self._ip_address}"
        )

    @property
    def name(self) -> str:
        """Return the name of the climate entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._mac}_climate"

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        return self._target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return hvac operation ie. heat, cool mode."""
        return self._hvac_mode

    @property
    def hvac_modes(self) -> List[HVACMode]:
        """Return the list of available hvac operation modes."""
        return self._attr_hvac_modes

    @property
    def fan_mode(self) -> Optional[str]:
        """Return the fan setting."""
        return self._fan_mode

    @property
    def fan_modes(self) -> Optional[List[str]]:
        """Return the list of available fan modes."""
        return self._attr_fan_modes

    @property
    def swing_mode(self) -> Optional[str]:
        """Return the swing setting."""
        return self._swing_mode

    @property
    def swing_modes(self) -> Optional[List[str]]:
        """Return the list of available swing modes."""
        return self._attr_swing_modes

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._max_temp

    @property
    def outside_temperature(self) -> Optional[float]:
        """Return the outside temperature."""
        return self._outside_temperature

    @property
    def current_humidity(self) -> Optional[int]:
        """Return the current humidity."""
        return self._current_humidity

    @property
    def last_temperature_adjustment(self) -> Optional[dict]:
        """Return information about the last temperature adjustment."""
        return self._last_temp_adjustment

    def get_temperature_adjustment_message(self) -> Optional[str]:
        """Return a user-friendly message about the last temperature adjustment."""
        if self._last_temp_adjustment:
            return self._last_temp_adjustment['message']
        return None

    def _validate_response(self, response: dict) -> None:
        """Validate response status codes from device."""
        if not response or 'responses' not in response:
            raise Exception("Invalid response format from device")

        for resp in response['responses']:
            rsc = resp.get('rsc')
            if rsc is None:
                continue

            if rsc in (2000, 2004):
                continue  # Success codes
            elif rsc == 4000:
                fr = resp.get('fr', 'unknown')
                raise Exception(f"Device rejected request to {fr} (error code: {rsc})")
            else:
                fr = resp.get('fr', 'unknown')
                raise Exception(f"Device error for {fr}: code {rsc}")

    async def _try_set_temperature(self, temperature: float) -> bool:
        """Try to set a specific temperature. Returns True if successful."""
        attr_name = HVAC_TO_TEMP_HEX.get(self.hvac_mode)
        if attr_name is None:
            _LOGGER.error(f"Cannot set temperature in {self.hvac_mode} mode.")
            return False

        temperature_hex = format(int(temperature * 2), '02x')
        _LOGGER.debug(f"Trying temperature {temperature} (hex: {temperature_hex}) using attribute {attr_name}")

        # Create the attribute for setting the temperature
        temp_attr = DaikinAttribute(attr_name, temperature_hex, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")

        # Log the request for debugging
        request = DaikinRequest([temp_attr]).serialize()
        _LOGGER.debug(f"Temperature set request: {request}")

        try:
            # Send the request to the device
            async with self._session.put(self.url, json=request) as response:
                data = await response.json()
                _LOGGER.debug(f"Temperature response: {data}")
                self._validate_response(data)
                return True
        except Exception as e:
            if "error code: 4000" in str(e):
                _LOGGER.debug(f"Temperature {temperature} rejected by device")
                return False
            else:
                # Re-raise non-temperature-range errors
                raise

    async def _search_valid_temperature(self, start_temp: float, direction: int) -> Optional[float]:
        """Search for a valid temperature in the given direction using optimized search."""
        # Reasonable temperature bounds (most AC units support 16-30°C)
        min_temp, max_temp = 16.0, 30.0

        # First, try just a few nearby temperatures (most likely to succeed)
        # This handles the common case where device rounds to nearest 0.5 or 1.0
        quick_tries = [0.5, 1.0, -0.5, -1.0] if direction > 0 else [-0.5, -1.0, 0.5, 1.0]

        for offset in quick_tries:
            test_temp = start_temp + offset
            if min_temp <= test_temp <= max_temp:
                if await self._try_set_temperature(test_temp):
                    return test_temp

        # If quick tries failed, do a linear search in the specified direction
        current_temp = start_temp
        for _ in range(10):  # Reduced from 15 for faster failure
            current_temp += direction * 0.5

            if current_temp < min_temp or current_temp > max_temp:
                break

            if await self._try_set_temperature(current_temp):
                return current_temp

        return None

    async def async_set_temperature(self, **kwargs):
        """Set temperature with smart clipping to nearest valid value."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        requested_temp = temperature

        _LOGGER.info("Temp change to " + str(temperature) + " requested.")
        attr_name = HVAC_TO_TEMP_HEX.get(self.hvac_mode)
        if attr_name is None:
            _LOGGER.error(f"Cannot set temperature in {self.hvac_mode} mode.")
            return

        # Try the exact temperature first
        if await self._try_set_temperature(temperature):
            # Success! Update the target temperature property
            self._target_temperature = temperature
            self._last_temp_adjustment = None
            # Don't call update here - let coordinator handle it
            return

        _LOGGER.info(f"Temperature {temperature} was rejected, trying smart clipping...")

        # If exact temp failed, try clipping upward first (toward warmer temps)
        # This handles the common case where requested temp is too cold
        final_temp = await self._search_valid_temperature(temperature, direction=1)

        if final_temp is None:
            # If that fails, try downward (toward cooler temps)
            final_temp = await self._search_valid_temperature(temperature, direction=-1)

        if final_temp is not None:
            # Success! Record the adjustment
            self._last_temp_adjustment = {
                'requested': requested_temp,
                'actual': final_temp,
                'message': f"Temperature adjusted from {requested_temp:.1f}°C to {final_temp:.1f}°C (nearest available)"
            }
            _LOGGER.info(self._last_temp_adjustment['message'])

            # Update the target temperature property
            self._target_temperature = final_temp
            # Don't call update here - let coordinator handle it
        else:
            # No valid temperature found
            error_msg = f"No valid temperature found near {requested_temp:.1f}°C. Device may have limited temperature range in {self.hvac_mode} mode."
            _LOGGER.error(error_msg)
            raise Exception(error_msg)

    async def _update_attribute(self, request: dict) -> None:
        _LOGGER.info(request)
        async with self._session.put(self.url, json=request) as response:
            data = await response.json()
            _LOGGER.info(data)
            self._validate_response(data)
        # Don't call update here - let coordinator handle it

    async def _update_state(self, state: bool):
        attribute = DaikinAttribute("p_01", "00" if not state else "01", ["e_1002", "e_A002"], "/dsiot/edge/adr_0100.dgc_status")
        await self._update_attribute(DaikinRequest([attribute]).serialize())

    async def async_turn_off(self):
        _LOGGER.info("Turned off")
        await self._update_state(False)

    async def async_turn_on(self):
        _LOGGER.info("Turned on")
        await self._update_state(True)

    def get_swing_state(self, data: dict) -> str:
        # Get the swing attribute names for the current HVAC mode
        vertical_attr_name, horizontal_attr_name = HVAC_MODE_TO_SWING_ATTR_NAMES[self.hvac_mode]

        # First try to find swing values in e_3001
        try:
            vertical = "F" in self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", vertical_attr_name)
            horizontal = "F" in self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", horizontal_attr_name)

            if horizontal and vertical:
                return SWING_BOTH
            elif horizontal:
                return SWING_HORIZONTAL
            elif vertical:
                return SWING_VERTICAL
            else:
                return SWING_OFF
        except Exception:
            # If we can't find swing values, default to OFF
            return SWING_OFF

    async def async_update(self):
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge/adr_0100.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0200.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0100.i_power.week_power?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge.adp_i"}
            ]
        }
        try:
            async with self._session.post(self.url, json=payload) as response:
                response.raise_for_status()
                data = await response.json()

            # Check if device is powered off
            power_status = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_A002", "p_01")

            if power_status == "00":
                self._hvac_mode = HVACMode.OFF
            else:
                # Get the mode
                mode_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", "p_01")
                self._hvac_mode = MODE_MAP.get(mode_hex, HVACMode.OFF)

                # Get swing mode
                self._swing_mode = self.get_swing_state(data)

            # Get current temperature (indoor)
            try:
                indoor_temp_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_A00B", "p_01")
                self._current_temperature = int(indoor_temp_hex, 16) if indoor_temp_hex else None
            except Exception:
                self._current_temperature = None

            # Get target temperature based on current mode
            if self._hvac_mode in HVAC_TO_TEMP_HEX:
                try:
                    name = HVAC_TO_TEMP_HEX.get(self._hvac_mode)
                    target_temp_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", name)
                    self._target_temperature = int(target_temp_hex, 16) / 2 if target_temp_hex else None
                except Exception:
                    self._target_temperature = None

            # Get outside temperature
            try:
                outside_temp_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0200.dgc_status", "dgc_status", "e_1003", "e_A00D", "p_01")
                if outside_temp_hex:
                    # Handle 4-character hex values (e.g., "4100") by only using first 2 chars
                    # Some devices return longer hex strings for temperature
                    temp_hex = outside_temp_hex[:2] if len(outside_temp_hex) > 2 else outside_temp_hex
                    self._outside_temperature = int(temp_hex, 16) / 2
                else:
                    self._outside_temperature = None
            except Exception as e:
                _LOGGER.debug(f"Error parsing outdoor temperature: {e}")
                self._outside_temperature = None

            # Get humidity
            try:
                humidity_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_A00B", "p_02")
                self._current_humidity = int(humidity_hex, 16) if humidity_hex else None
            except Exception:
                self._current_humidity = None

            # Get fan mode (if device is in an appropriate mode)
            if self._hvac_mode in HVAC_MODE_TO_FAN_SPEED_ATTR_NAME:
                fan_speed_attr = HVAC_MODE_TO_FAN_SPEED_ATTR_NAME[self._hvac_mode]
                entity_name = FAN_SPEED_ENTITY_MAP.get(fan_speed_attr)

                try:
                    if entity_name == "e_3003":
                        # Fan speed is in e_3003 (AUTO mode)
                        fan_speed_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3003", fan_speed_attr)
                        # For e_3003, we need to use the E3003 map
                        for mode, hex_val in FAN_MODE_MAP_E3003.items():
                            if fan_speed_hex == hex_val:
                                self._fan_mode = mode
                                break
                    else:
                        # Fan speed is in e_3001 (other modes)
                        fan_speed_hex = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", fan_speed_attr)
                        # Use the regular fan mode map
                        for mode, hex_val in FAN_MODE_MAP.items():
                            if fan_speed_hex == hex_val:
                                self._fan_mode = mode
                                break
                except Exception:
                    self._fan_mode = HAFanMode.FAN_AUTO  # Default fallback

            # Get energy data if available
            try:
                self._runtime_today = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.i_power.week_power", "week_power", "today_runtime")
                energy_data = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.i_power.week_power", "week_power", "datas")
                if isinstance(energy_data, list) and len(energy_data) > 0:
                    self._energy_today = energy_data[-1] / 1000  # Convert to kWh
            except Exception:
                pass

        except Exception as e:
            _LOGGER.error(f"Error updating Daikin AC status: {e}")

    def find_value_by_pn(self, data: dict, fr: str, *keys):
        """Find values in nested response data."""
        try:
            # Find the correct response based on 'fr'
            response_data = None
            for response in data.get('responses', []):
                if response.get('fr') == fr:
                    response_data = response.get('pc')
                    break

            if response_data is None:
                return None

            # Navigate through the nested structure using the keys
            current = response_data
            for key in keys:
                if key == keys[-1]:  # Last key - look for 'pv'
                    if isinstance(current, list):
                        for item in current:
                            if isinstance(item, dict) and item.get('pn') == key:
                                return item.get('pv')
                    elif isinstance(current, dict) and current.get('pn') == key:
                        return current.get('pv')
                    return None
                else:  # Navigate deeper
                    if isinstance(current, list):
                        found = False
                        for item in current:
                            if isinstance(item, dict) and item.get('pn') == key:
                                current = item.get('pch', [])
                                found = True
                                break
                        if not found:
                            return None
                    elif isinstance(current, dict) and current.get('pn') == key:
                        current = current.get('pch', [])
                    else:
                        return None

            return None
        except Exception as e:
            _LOGGER.debug(f"Error finding value by pn: {e}")
            return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        _LOGGER.info("Mode change to " + str(hvac_mode) + " requested.")
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            # First turn on the device
            await self.async_turn_on()

            # Then set the mode
            mode_hex = REVERSE_MODE_MAP.get(hvac_mode)
            if mode_hex:
                mode_attr = DaikinAttribute("p_01", mode_hex, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
                request = DaikinRequest([mode_attr]).serialize()
                await self._update_attribute(request)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        _LOGGER.info("Fan mode change to " + str(fan_mode) + " requested.")
        if self._hvac_mode not in HVAC_MODE_TO_FAN_SPEED_ATTR_NAME:
            _LOGGER.error(f"Cannot set fan mode in {self._hvac_mode} mode.")
            return

        fan_speed_attr = HVAC_MODE_TO_FAN_SPEED_ATTR_NAME[self._hvac_mode]
        entity_name = FAN_SPEED_ENTITY_MAP.get(fan_speed_attr)

        if entity_name == "e_3003":
            # Use the E3003 map for AUTO mode
            fan_mode_hex = FAN_MODE_MAP_E3003.get(fan_mode)
            if fan_mode_hex:
                fan_attr = DaikinAttribute(fan_speed_attr, fan_mode_hex, ["e_1002", "e_3003"], "/dsiot/edge/adr_0100.dgc_status")
        else:
            # Use the regular map for other modes
            fan_mode_hex = FAN_MODE_MAP.get(fan_mode)
            if fan_mode_hex:
                fan_attr = DaikinAttribute(fan_speed_attr, fan_mode_hex, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")

        if fan_mode_hex:
            request = DaikinRequest([fan_attr]).serialize()
            await self._update_attribute(request)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        _LOGGER.info("Swing mode change to " + str(swing_mode) + " requested.")

        # Get the swing attribute names for the current HVAC mode
        if self._hvac_mode not in HVAC_MODE_TO_SWING_ATTR_NAMES:
            _LOGGER.error(f"Cannot set swing mode in {self._hvac_mode} mode.")
            return

        vertical_attr_name, horizontal_attr_name = HVAC_MODE_TO_SWING_ATTR_NAMES[self._hvac_mode]

        # Create attributes for both vertical and horizontal swing
        attributes = []

        if swing_mode in (SWING_OFF, SWING_HORIZONTAL):
            vertical_value = TURN_OFF_SWING_AXIS
        else:
            vertical_value = TURN_ON_SWING_AXIS

        if swing_mode in (SWING_OFF, SWING_VERTICAL):
            horizontal_value = TURN_OFF_SWING_AXIS
        else:
            horizontal_value = TURN_ON_SWING_AXIS

        # Add vertical swing attribute
        vertical_attr = DaikinAttribute(vertical_attr_name, vertical_value, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
        attributes.append(vertical_attr)

        # Add horizontal swing attribute
        horizontal_attr = DaikinAttribute(horizontal_attr_name, horizontal_value, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
        attributes.append(horizontal_attr)

        request = DaikinRequest(attributes).serialize()
        await self._update_attribute(request)