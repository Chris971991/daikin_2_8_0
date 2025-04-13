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
        try:
            response = await hass.async_add_executor_job(lambda: requests.post(self.url, json=payload))
            response.raise_for_status()
            data = response.json()
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
        return {
            "outside_temperature": self._outside_temperature,
            "runtime_today": self._runtime_today,
            "energy_today": self._energy_today
        }

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
        """Find a value in the response by path.
        
        This method handles the specific structure of the Daikin API response.
        """
        try:
            # Add detailed logging
            _LOGGER.debug(f"Finding value for path: {fr} -> {' -> '.join(keys)}")
            
            # Check if 'responses' exists in data
            if 'responses' not in data:
                _LOGGER.debug("No 'responses' key in data")
                return None
                
            # Find the response with matching 'fr'
            matching_responses = [x for x in data['responses'] if x.get('fr') == fr]
            if not matching_responses:
                _LOGGER.debug(f"No response with fr={fr} found")
                return None
                
            # Get the response
            response = matching_responses[0]
            
            # Get the PC object
            pc = response.get('pc', {})
            
            # Skip the first key (usually 'dgc_status' or 'week_power') since it's the value of pc['pn']
            if len(keys) > 0 and pc.get('pn') == keys[0]:
                keys = keys[1:]
            
            # Special case for outside temperature
            if 'e_A00D' in keys and 'p_01' in keys and keys[-1] == 'p_01':
                for item in pc.get('pch', []):
                    if item.get('pn') == 'e_1003':
                        for sub_item in item.get('pch', []):
                            if sub_item.get('pn') == 'e_A00D':
                                for p_item in sub_item.get('pch', []):
                                    if p_item.get('pn') == 'p_01':
                                        _LOGGER.debug(f"Found outside temp: {p_item.get('pv')}")
                                        return p_item.get('pv')
            
            # Special case for current temperature and humidity
            if 'e_A00B' in keys and ('p_01' in keys or 'p_02' in keys):
                for item in pc.get('pch', []):
                    if item.get('pn') == 'e_1002':
                        for sub_item in item.get('pch', []):
                            if sub_item.get('pn') == 'e_A00B':
                                for p_item in sub_item.get('pch', []):
                                    if p_item.get('pn') == keys[-1]:  # p_01 or p_02
                                        _LOGGER.debug(f"Found temperature/humidity: {p_item.get('pv')}")
                                        return p_item.get('pv')
            
            # Special case for power state
            if 'e_A002' in keys and 'p_01' in keys:
                for item in pc.get('pch', []):
                    if item.get('pn') == 'e_1002':
                        for sub_item in item.get('pch', []):
                            if sub_item.get('pn') == 'e_A002':
                                for p_item in sub_item.get('pch', []):
                                    if p_item.get('pn') == 'p_01':
                                        _LOGGER.debug(f"Found power state: {p_item.get('pv')}")
                                        return p_item.get('pv')
            
            # Special case for HVAC mode
            if 'e_3001' in keys and 'p_01' in keys:
                for item in pc.get('pch', []):
                    if item.get('pn') == 'e_1002':
                        for sub_item in item.get('pch', []):
                            if sub_item.get('pn') == 'e_3001':
                                for p_item in sub_item.get('pch', []):
                                    if p_item.get('pn') == 'p_01':
                                        _LOGGER.debug(f"Found HVAC mode: {p_item.get('pv')}")
                                        return p_item.get('pv')
            
            # Special case for week_power data
            if fr == '/dsiot/edge/adr_0100.i_power.week_power':
                if pc.get('pn') == 'week_power':
                    for item in pc.get('pch', []):
                        if item.get('pn') == 'today_runtime':
                            _LOGGER.debug(f"Found today runtime: {item.get('pv')}")
                            return item.get('pv')
                        elif item.get('pn') == 'datas':
                            _LOGGER.debug(f"Found week power data: {item.get('pv')}")
                            return item.get('pv')
            
            # If we couldn't find the value with direct access, log it
            _LOGGER.debug(f"Could not find value for path: {fr} -> {' -> '.join(keys)}")
            return None
            
        except Exception as e:
            _LOGGER.debug(f"Error in find_value_by_pn: {e}")
            return None

    @staticmethod
    def hex_to_temp(value: str, divisor=2) -> Optional[float]:
        """Convert hex value to temperature.
        
        Returns None if value is None.
        """
        if value is None:
            return None
        try:
            return int(value[:2], 16) / divisor
        except (ValueError, IndexError, TypeError) as e:
            _LOGGER.debug(f"Error converting hex to temp: {e}")
            return None

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
        
        vertical_value = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", vertical_attr_name)
        vertical = "F" in vertical_value if vertical_value is not None else False
        
        horizontal_value = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", horizontal_attr_name)
        horizontal = "F" in horizontal_value if horizontal_value is not None else False

        if horizontal and vertical:
            return SWING_BOTH
        if horizontal:
            return SWING_HORIZONTAL
        if vertical:
            return SWING_VERTICAL
        
        return SWING_OFF
        
    def _extract_outside_temperature(self, data: dict) -> Optional[float]:
        """Extract the outside temperature from the API response."""
        _LOGGER.debug("Attempting to find outside temperature")
        
        # Try to find the outside temperature
        outside_temp_hex = self.find_value_by_pn(data, '/dsiot/edge/adr_0200.dgc_status', 'dgc_status', 'e_1003', 'e_A00D', 'p_01')
        
        if outside_temp_hex is not None:
            try:
                # Convert the hex value to a float
                if isinstance(outside_temp_hex, str):
                    # Handle string values (hex)
                    temp = self.hex_to_temp(outside_temp_hex)
                elif isinstance(outside_temp_hex, (int, float)):
                    # Handle numeric values
                    temp = float(outside_temp_hex) / 2
                else:
                    # Handle other types
                    _LOGGER.warning(f"Unexpected outside temperature type: {type(outside_temp_hex)}")
                    return None
                
                _LOGGER.debug(f"Converted outside temperature: {temp}")
                return temp
            except Exception as e:
                _LOGGER.error(f"Error converting outside temperature: {e}")
                return None
        else:
            _LOGGER.debug("Outside temperature not available")
            return None

    def update(self):
        """Fetch new state data for the entity."""
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge/adr_0100.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0200.dgc_status?filter=pv,pt,md"},
                {"op": 2, "to": "/dsiot/edge/adr_0100.i_power.week_power?filter=pv,pt,md"}
            ]
        }

        try:
            response = requests.post(self.url, json=payload)
            response.raise_for_status()
            data = response.json()
            _LOGGER.debug(data)

            # Set the HVAC mode.
            power_state = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_A002", "p_01")
            is_off = power_state == "00" if power_state is not None else True
            
            mode_value = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_3001', 'p_01')
            self._hvac_mode = HVACMode.OFF if is_off else MODE_MAP.get(mode_value, HVACMode.OFF)
            # Get the outside temperature
            self._outside_temperature = self._extract_outside_temperature(data)

            # Only set the target temperature if this mode allows it. Otherwise, it should be set to none.
            name = HVAC_TO_TEMP_HEX.get(self._hvac_mode)
            if name is not None:
                target_temp_hex = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_3001', name)
                if target_temp_hex is not None:
                    self._target_temperature = self.hex_to_temp(target_temp_hex)
                else:
                    self._target_temperature = None
            else:
                self._target_temperature = None
            
            # For some reason, this hex value does not get the 'divide by 2' treatment. My only assumption as to why this might be is because the level of granularity
            # for this temperature is limited to integers. So the passed divisor is 1.
            current_temp_hex = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_A00B', 'p_01')
            if current_temp_hex is not None:
                self._current_temperature = self.hex_to_temp(current_temp_hex, divisor=1)
            else:
                self._current_temperature = None

            # If we cannot find a name for this hvac_mode's fan speed, it is automatic. This is the case for dry.
            fan_mode_key_name = HVAC_MODE_TO_FAN_SPEED_ATTR_NAME.get(self.hvac_mode)
            if fan_mode_key_name is not None:
                fan_mode_value = self.find_value_by_pn(data, "/dsiot/edge/adr_0100.dgc_status", "dgc_status", "e_1002", "e_3001", HVAC_MODE_TO_FAN_SPEED_ATTR_NAME[self.hvac_mode])
                if fan_mode_value is not None and fan_mode_value in REVERSE_FAN_MODE_MAP:
                    self._fan_mode = REVERSE_FAN_MODE_MAP[fan_mode_value]
                else:
                    self._fan_mode = HAFanMode.FAN_AUTO
            else:
                self._fan_mode = HAFanMode.FAN_AUTO

            humidity_hex = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.dgc_status', 'dgc_status', 'e_1002', 'e_A00B', 'p_02')
            if humidity_hex is not None:
                self._current_humidity = int(humidity_hex, 16)
            else:
                self._current_humidity = None

            if not self.hvac_mode == HVACMode.OFF:
                self._swing_mode = self.get_swing_state(data)
            
            energy_data = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.i_power.week_power', 'week_power', 'datas')
            if energy_data is not None and len(energy_data) > 0:
                self._energy_today = int(energy_data[-1])
            else:
                self._energy_today = 0
                
            runtime_data = self.find_value_by_pn(data, '/dsiot/edge/adr_0100.i_power.week_power', 'week_power', 'today_runtime')
            if runtime_data is not None:
                self._runtime_today = int(runtime_data)
            else:
                self._runtime_today = 0
            
        except Exception as e:
            _LOGGER.error(f"Error updating Daikin AC: {e}")
