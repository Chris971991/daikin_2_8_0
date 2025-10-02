"""Config flow for Daikin 2.8.0 integration."""
from __future__ import annotations

import logging
import ipaddress
from typing import Any

import voluptuous as vol
from aiohttp import ClientError

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.const import CONF_IP_ADDRESS, CONF_FRIENDLY_NAME
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

class DaikinFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Daikin config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                # Validate the IP address
                ipaddress.ip_address(user_input[CONF_IP_ADDRESS])
                
                # Test the connection
                await self._test_connection(user_input[CONF_IP_ADDRESS])
                
                # Create a unique_id based on IP (this will be replaced with MAC later)
                await self.async_set_unique_id(user_input[CONF_IP_ADDRESS])
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(
                    title=user_input.get(CONF_FRIENDLY_NAME, f"Daikin {user_input[CONF_IP_ADDRESS]}"),
                    data=user_input,
                )
            except ValueError:
                errors["base"] = "invalid_ip"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IP_ADDRESS): str,
                    vol.Optional(CONF_FRIENDLY_NAME): str,
                }
            ),
            errors=errors,
        )
        
    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        """Handle import from YAML."""
        return await self.async_step_user(user_input)

    async def _test_connection(self, ip_address):
        """Test the connection to the Daikin AC."""
        url = f"http://{ip_address}/dsiot/multireq"
        payload = {
            "requests": [
                {"op": 2, "to": "/dsiot/edge.adp_i"}
            ]
        }

        session = async_get_clientsession(self.hass)
        try:
            async with session.post(url, json=payload, timeout=10) as response:
                response.raise_for_status()
                return True
        except ClientError as err:
            _LOGGER.error("Error connecting to Daikin AC: %s", err)
            raise ConnectionError from err