"""Daikin 2.8.0 Climate integration for Home Assistant."""
import logging
import requests
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

FAN_MODE_MAP = {
    HAFanMode.FAN_AUTO : "0A00",
    HAFanMode.FAN_QUIET : "0B00",
    HAFanMode.FAN_LEVEL1 : "0300",
    HAFanMode.FAN_LEVEL2 : "0400",
    HAFanMode.FAN_LEVEL3 : "0500",
    HAFanMode.FAN_LEVEL4 : "0600",
    HAFanMode.FAN_LEVEL5 : "0700"
}

# Vertical, horizontal
HVAC_MODE_TO_SWING_ATTR_NAMES = {
    HVACMode.AUTO : ("p_20", "p_21"),
    HVACMode.COOL : ("p_05", "p_06"),
    HVACMode.HEAT : ("p_07", "p_08"),
    HVACMode.FAN_ONLY : ("p_24", "p_25"),
    HVACMode.DRY : ("p_22", "p_23")
}

HVAC_MODE_TO_FAN_SPEED_ATTR_NAME = {
    HVACMode.AUTO : "p_26",
    HVACMode.COOL : "p_09",
    HVACMode.HEAT : "p_0A",
    HVACMode.FAN_ONLY : "p_28",
    # HVACMode.DRY : "dummy" There is no fan mode for dry. It's always automatic.
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

        def get_existing_index(name: str, children: list[dict]) -> int:
            for index, child in enumerate(children):
                if child.get("pn") == name:
                    return index
            return -1
        
        def get_existing_to(to: str, requests: list[dict]) -> bool:
            for request in requests:
                this_to = request.get("to")
                if this_to == to:
                    return request
            return None

        for attribute in self.attributes:
            to = get_existing_to(attribute.to, payload['requests'])
            if to is None:
                payload['requests'].append({
                    'op': 3,
                    'pc' : {
                        "pn" : "dgc_status",
                        "pch" : []
                    },
                    "to": attribute.to
                })
                to = payload['requests'][-1]
            entry = to['pc']['pch']
            for pn in attribute.path:
                index = get_existing_index(pn, entry)
                if index == -1:
                    entry.append({"pn": pn, "pch": []})
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

    def __init__(self, ip_address: str, friendly_name: str):
        """Initialize the climate entity."""
        self._ip_address = ip_address
        self._friendly_name = friendly_name
        self._name = f"{friendly_name} Climate"
        self.url = f"http://{ip_address}/dsiot/multireq"
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

    async def initialize_unique_id(self, hass):
        """Get the MAC address to use as the unique ID."""
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge.adp_i"}
            ]
        }
        response = await hass.async_add_executor_job(lambda: requests.post(self.url, json=payload))
        response.raise_for_status()
        data = response.json()
        self._mac = format_mac(self.find_value_by_pn(data, "/dsiot/edge.adp_i", "adp_i", "mac"))
        _LOGGER.info(f"Initialized Daikin AC with MAC: {self._mac}")

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
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def fan_mode(self):
        """Return current operation."""
        return self._fan_mode

    @property
    def swing_mode(self):
        return self._swing_mode

    def set_fan_mode(self, fan_mode: str):
        mode = FAN_MODE_MAP[fan_mode]
        name = HVAC_MODE_TO_FAN_SPEED_ATTR_NAME.get(self.hvac_mode)

        # If in dry mode for example, you cannot set the fan speed. So we ignore anything we cant find in this map.
        if name is not None:
            mode_attr = DaikinAttribute(name, mode, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
            self.update_attribute(DaikinRequest([mode_attr]).serialize())
        else:
            self._fan_mode = HAFanMode.FAN_AUTO

    def set_swing_mode(self, swing_mode: str):
        if self.hvac_mode == HVACMode.OFF:
            return
        vertical_axis_command = TURN_OFF_SWING_AXIS if swing_mode in (SWING_OFF, SWING_HORIZONTAL) else TURN_ON_SWING_AXIS
        horizontal_axis_command = TURN_OFF_SWING_AXIS if swing_mode in (SWING_OFF, SWING_VERTICAL) else TURN_ON_SWING_AXIS
        vertical_attr_name, horizontal_attr_name = HVAC_MODE_TO_SWING_ATTR_NAMES[self.hvac_mode]
        self.update_attribute(
            DaikinRequest(
                [
                    DaikinAttribute(horizontal_attr_name, horizontal_axis_command, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status"),
                    DaikinAttribute(vertical_attr_name, vertical_axis_command, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
                ]
            ).serialize()
        )

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._max_temp

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._min_temp

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_temperature(self):
        return self._current_temperature

    @property
    def current_humidity(self) -> int:
        return self._current_humidity

    @property
    def unique_id(self):
        return f"{self._mac}_climate"

    @property
    def extra_state_attributes(self):
        """Return entity specific state attributes."""
        attributes = {
            "outside_temperature": self._outside_temperature,
            "runtime_today": self._runtime_today,
            "energy_today": self._energy_today
        }
        _LOGGER.info(f"Returning attributes: {attributes}")
        return attributes

    def set_hvac_mode(self, hvac_mode):
        _LOGGER.info("Set Hvac mode to " + str(hvac_mode))

        if hvac_mode == HVACMode.OFF:
            self.turn_off()
        else:
            new_mode = REVERSE_MODE_MAP.get(hvac_mode)
            if new_mode is None:
                raise Exception(f"Unknown hvac mode {hvac_mode}")
            attribute = DaikinAttribute("p_01", new_mode, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")

            # Potentially add the turn on attribute here, unsure.
            self.turn_on()
            self.update_attribute(DaikinRequest([attribute]).serialize())

    @staticmethod
    def find_value_by_pn(data:dict, fr: str, *keys):
        """Find a value in the nested response data structure.
        
        Args:
            data: The response data
            fr: The response path to look for
            *keys: The sequence of keys to navigate through
            
        Returns:
            The value found at the specified path
            
        Raises:
            Exception: If the path is not found
        """
        # First, find the response with the matching 'fr' value
        matching_responses = [x for x in data.get('responses', []) if x.get('fr') == fr]
        
        if not matching_responses:
            available_responses = [x.get('fr') for x in data.get('responses', [])]
            raise Exception(f'Response path {fr} not found. Available paths: {available_responses}')
        
        # Extract the 'pc' field from the matching response
        data = [x.get('pc', {}) for x in matching_responses]
        
        # Log the initial data structure for debugging
        _LOGGER.debug(f"Initial data structure for path {fr}: {data}")
        
        # Navigate through the keys
        while keys:
            current_key = keys[0]
            keys = keys[1:]
            found = False
            
            for pcs in data:
                if pcs.get('pn') == current_key:
                    if not keys:
                        if 'pv' in pcs:
                            return pcs['pv']
                        else:
                            raise Exception(f'Value not found for key {current_key}')
                    
                    if 'pch' in pcs:
                        data = pcs['pch']
                        found = True
                        break
                    else:
                        raise Exception(f'No children found for key {current_key}')
            
            if not found:
                available_keys = [pcs.get('pn') for pcs in data if 'pn' in pcs]
                raise Exception(f'Key {current_key} not found. Available keys: {available_keys}')

    @staticmethod
    def hex_to_temp(value: str, divisor=2) -> float:
        """Convert temperature value to float.
        
        For values that look like hex codes (e.g., '1400'), convert from hex.
        For values that look like direct numbers (e.g., '12.5'), convert directly.
        """
        _LOGGER.info(f"Converting temperature value: {value} (type: {type(value)})")
        
        # First check if it's a direct numeric value with a decimal point
        if isinstance(value, str) and '.' in value:
            try:
                result = float(value)
                _LOGGER.info(f"Converted decimal string to float: {value} -> {result}")
                return result
            except (ValueError, TypeError) as e:
                _LOGGER.info(f"Failed to convert decimal string: {e}")
                pass
                
        # Check if it's a typical hex temperature code (4 chars, no decimal)
        if isinstance(value, str) and len(value) == 4 and all(c in '0123456789ABCDEFabcdef' for c in value):
            try:
                result = int(value[:2], 16) / divisor
                _LOGGER.info(f"Converted 4-char hex to float: {value} -> {result} (hex: {value[:2]} -> {int(value[:2], 16)})")
                return result
            except (ValueError, TypeError) as e:
                _LOGGER.info(f"Failed to convert 4-char hex: {e}")
                pass
        
        # For any other format, try direct conversion first
        try:
            result = float(value)
            _LOGGER.info(f"Converted direct to float: {value} -> {result}")
            return result
        except (ValueError, TypeError) as e:
            _LOGGER.info(f"Failed direct float conversion: {e}")
            # If that fails, try hex as a last resort
            try:
                if isinstance(value, str) and len(value) >= 2:
                    result = int(value[:2], 16) / divisor
                    _LOGGER.info(f"Converted string to hex as last resort: {value} -> {result}")
                    return result
            except (ValueError, TypeError) as e:
                _LOGGER.info(f"Failed last resort hex conversion: {e}")
                pass
                
        # If all else fails, return 0
        _LOGGER.error(f"Failed to convert temperature value: {value}")
        return 0

    def set_temperature(self, temperature: float, **kwargs):
        _LOGGER.info("Temp change to " + str(temperature) + " requested.")
        attr_name = HVAC_TO_TEMP_HEX.get(self.hvac_mode)
        if attr_name is None:
            _LOGGER.error(f"Cannot set temperature in {self.hvac_mode} mode.")
            return

        temperature_hex = format(int(temperature * 2), '02x') 
        temp_attr = DaikinAttribute(attr_name, temperature_hex, ["e_1002", "e_3001"], "/dsiot/edge/adr_0100.dgc_status")
        self.update_attribute(DaikinRequest([temp_attr]).serialize())

    def update_attribute(self, request: dict, *keys) -> None:
        _LOGGER.info(request)
        response = requests.put(self.url, json=request).json()
        _LOGGER.info(response)
        if response['responses'][0]['rsc'] != 2004:
            raise Exception(f"An exception occured:\n{response}")

        self.update()

    def _update_state(self, state: bool):
        attribute = DaikinAttribute("p_01", "00" if not state else "01", ["e_1002", "e_A002"], "/dsiot/edge/adr_0100.dgc_status")
        self.update_attribute(DaikinRequest([attribute]).serialize())

    def turn_off(self):
        _LOGGER.info("Turned off")
        self._update_state(False)

    def turn_on(self):
        _LOGGER.info("Turned on")
        self._update_state(True)

    def get_swing_state(self, data: dict) -> str:
        # The number of zeros in the response seems strange. Don't have time to work out, so this should work
        vertical_attr_name, horizontal_attr_name = HVAC_MODE_TO_SWING_ATTR_NAMES[self.hvac_mode]
        vertical = "F" in self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", vertical_attr_name)
        horizontal = "F" in self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", horizontal_attr_name)

        if horizontal and vertical:
            return SWING_BOTH
        if horizontal:
            return SWING_HORIZONTAL
        if vertical:
            return SWING_VERTICAL
        
        return SWING_OFF

    def update(self):
        """Fetch new state data for the entity."""
        # Use the exact same structure as the test code
        # Add a specific request for outside temperature sensor data
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge/adr_0100.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0200.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0100.i_power.week_power?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge.adp_i"},
                # Add specific request for outside temperature sensor
                {"op": 2, "to": "/dsiot/edge/adr_0200.sensor?filter=pv,pt,md"}
            ]
        }

        try:
            response = requests.post(self.url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # Enhanced debugging to understand the API response structure
            _LOGGER.info("Full API response: %s", data)
            
            # Specifically log the structure of the second request which should contain outside temperature
            for resp in data.get('responses', []):
                if resp.get('fr') == '/dsiot/edge/adr_0200.dgc_status':
                    _LOGGER.info("Found adr_0200 response: %s", resp)
                    # Explore the structure to find potential paths to outside temperature
                    if 'pc' in resp:
                        _LOGGER.info("PC structure: %s", resp['pc'])
                elif resp.get('fr') == '/dsiot/edge/adr_0200.sensor':
                    _LOGGER.info("Found sensor response: %s", resp)
                    if 'pc' in resp:
                        _LOGGER.info("Sensor PC structure: %s", resp['pc'])

            # Set the HVAC mode
            try:
                is_off = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_A002", "p_01") == "00"
                self._hvac_mode = HVACMode.OFF if is_off else MODE_MAP[self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_3001', 'p_01')]
            except Exception as e:
                _LOGGER.warning(f"Error setting HVAC mode: {e}")

            # For outside temperature - try multiple possible paths
            outside_temp_found = False
            
            # List of possible paths to try for outside temperature
            outside_temp_paths = [
                # Original path
                ['/dsiot/edge/adr_0200.dgc_status', 'dgc_status', 'e_1003', 'e_A00D', 'p_01'],
                # Alternative paths to try
                ['/dsiot/edge/adr_0200.dgc_status', 'dgc_status', 'e_1002', 'e_A00D', 'p_01'],
                ['/dsiot/edge/adr_0200.dgc_status', 'dgc_status', 'e_1002', 'e_A002', 'p_01'],
                ['/dsiot/edge/adr_0200.dgc_status', 'dgc_status', 'e_1002', 'e_A00B', 'p_01'],
                ['/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_A00D', 'p_01']
            ]
            
            # First try the standard paths
            for path in outside_temp_paths:
                try:
                    _LOGGER.info(f"Trying outside temperature path: {path}")
                    outside_temp_hex = self.find_value_by_pn(data, *path)
                    _LOGGER.info(f"Found outside temperature hex value: {outside_temp_hex}")
                    self._outside_temperature = self.hex_to_temp(outside_temp_hex)
                    _LOGGER.info(f"Successfully read outside temperature: {self._outside_temperature}°C from hex value {outside_temp_hex} using path {path}")
                    outside_temp_found = True
                    break
                except Exception as e:
                    _LOGGER.debug(f"Failed to read outside temperature with path {path}: {e}")
            
            # If not found, try the dedicated sensor endpoint we added
            if not outside_temp_found:
                try:
                    _LOGGER.info("Trying to read outside temperature from dedicated sensor endpoint")
                    # Try different possible paths in the sensor endpoint
                    sensor_paths = [
                        ['/dsiot/edge/adr_0200.sensor', 'sensor', 'temperature', 'outside'],
                        ['/dsiot/edge/adr_0200.sensor', 'sensor', 'outside_temp'],
                        ['/dsiot/edge/adr_0200.sensor', 'sensor', 'temp_outside']
                    ]
                    
                    for sensor_path in sensor_paths:
                        try:
                            _LOGGER.info(f"Trying sensor path: {sensor_path}")
                            outside_temp_value = self.find_value_by_pn(data, *sensor_path)
                            _LOGGER.info(f"Found outside temperature value from sensor: {outside_temp_value} (type: {type(outside_temp_value)})")
                            
                            # Check if the value is already a number or needs conversion
                            if isinstance(outside_temp_value, (int, float)):
                                self._outside_temperature = float(outside_temp_value)
                                _LOGGER.info(f"Converted direct numeric value to: {self._outside_temperature}")
                            else:
                                # Try to convert from hex if it's a string
                                old_value = self._outside_temperature
                                self._outside_temperature = self.hex_to_temp(outside_temp_value)
                                _LOGGER.info(f"Converted from hex/string: {outside_temp_value} -> {self._outside_temperature} (was: {old_value})")
                            
                            _LOGGER.info(f"Successfully read outside temperature from sensor endpoint: {self._outside_temperature}°C using path {sensor_path}")
                            outside_temp_found = True
                            break
                        except Exception as e:
                            _LOGGER.debug(f"Failed to read outside temperature from sensor path {sensor_path}: {e}")
                except Exception as e:
                    _LOGGER.debug(f"Failed to read from sensor endpoint: {e}")
            
            if not outside_temp_found:
                _LOGGER.error("Could not find outside temperature in any of the expected paths")
                if self._outside_temperature is None:
                    self._outside_temperature = 0

            # Get target temperature
            try:
                name = HVAC_TO_TEMP_HEX.get(self._hvac_mode)
                if name is not None:
                    self._target_temperature = self.hex_to_temp(self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_3001', name))
                else:
                    self._target_temperature = None
            except Exception as e:
                _LOGGER.warning(f"Error setting target temperature: {e}")
            
            # Get current temperature
            try:
                self._current_temperature = self.hex_to_temp(
                    self.find_value_by_pn(
                        data,
                        '/dsiot/edge/adr_0100.dgc_status',
                        'dgc_status',
                        'e_1002',
                        'e_A00B',
                        'p_01'
                    ),
                    divisor=1
                )
            except Exception as e:
                _LOGGER.warning(f"Error reading current temperature: {e}")
                if self._current_temperature is None:
                    self._current_temperature = 0

            # Get fan mode
            try:
                fan_mode_key_name = HVAC_MODE_TO_FAN_SPEED_ATTR_NAME.get(self.hvac_mode)
                if fan_mode_key_name is not None:
                    self._fan_mode = REVERSE_FAN_MODE_MAP[self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", HVAC_MODE_TO_FAN_SPEED_ATTR_NAME[self.hvac_mode])]
                else:
                    self._fan_mode = HAFanMode.FAN_AUTO
            except Exception as e:
                _LOGGER.warning(f"Error reading fan mode: {e}")

            # Get humidity
            try:
                self._current_humidity = int(
                    self.find_value_by_pn(
                        data,
                        '/dsiot/edge/adr_0100.dgc_status',
                        'dgc_status',
                        'e_1002',
                        'e_A00B',
                        'p_02'
                    ),
                    16
                )
            except Exception as e:
                _LOGGER.warning(f"Error reading humidity: {e}")
                if self._current_humidity is None:
                    self._current_humidity = 0

            # Get swing mode
            try:
                if not self.hvac_mode == HVACMode.OFF:
                    self._swing_mode = self.get_swing_state(data)
            except Exception as e:
                _LOGGER.warning(f"Error reading swing mode: {e}")
            
            # For energy today:
            try:
                energy_data = self.find_value_by_pn(
                    data,
                    '/dsiot/edge/adr_0100.i_power.week_power',
                    'week_power',
                    'datas'
                )
                if isinstance(energy_data, list) and len(energy_data) > 0:
                    self._energy_today = int(energy_data[-1])
                    _LOGGER.info(f"Successfully read energy today: {self._energy_today}")
            except Exception as e:
                _LOGGER.error(f"Error reading energy today: {e}")
                if self._energy_today is None:
                    self._energy_today = 0
            
            # For runtime today:
            try:
                runtime = self.find_value_by_pn(
                    data,
                    '/dsiot/edge/adr_0100.i_power.week_power',
                    'week_power',
                    'today_runtime'
                )
                self._runtime_today = int(runtime)
                _LOGGER.info(f"Successfully read runtime today: {self._runtime_today} minutes")
            except Exception as e:
                _LOGGER.error(f"Error reading runtime today: {e}")
                if self._runtime_today is None:
                    self._runtime_today = 0
            
        except Exception as e:
            _LOGGER.error(f"Error updating Daikin AC: {e}")