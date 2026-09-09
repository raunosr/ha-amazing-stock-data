"""Optional authenticated history transport for Amazing Stock Card."""

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PERIODS
from .provider import AvanzaHistory, HistoryError


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    websocket_api.async_register_command(hass, ws_history)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN] = {"avanza": AvanzaHistory(async_get_clientsession(hass))}
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    providers = hass.data.pop(DOMAIN, {})
    for provider in providers.values():
        await provider.close()
    return True


@websocket_api.websocket_command({
    vol.Required("type"): "amazing_stock_data/history",
    vol.Required("entity_id"): vol.All(str, vol.Match(r"^sensor\.[a-z0-9_]+$")),
    vol.Required("provider"): "avanza",
    vol.Required("instrument_id"): vol.All(str, vol.Match(r"^[0-9]{1,12}$")),
    vol.Required("period"): vol.In(PERIODS),
})
@websocket_api.async_response
async def ws_history(hass: HomeAssistant, connection, msg: dict) -> None:
    """Only HA users allowed to read the chosen sensor may request its history."""
    entity_id = msg["entity_id"]
    if not connection.user.permissions.check_entity(entity_id, POLICY_READ):
        connection.send_error(msg["id"], "unauthorized", "Entity read permission required")
        return
    if hass.states.get(entity_id) is None:
        connection.send_error(msg["id"], "entity_not_found", "Price sensor not found")
        return
    provider = hass.data.get(DOMAIN, {}).get(msg["provider"])
    if provider is None:
        connection.send_error(msg["id"], "not_loaded", "Set up Amazing Stock Data in Devices & services")
        return
    try:
        result = await provider.history(msg["instrument_id"], msg["period"])
    except HistoryError as error:
        connection.send_error(msg["id"], error.code, "Market history could not be loaded")
    else:
        connection.send_result(msg["id"], result)
