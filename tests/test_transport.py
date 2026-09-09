"""Exercise HA-facing permission and lifecycle logic with lightweight HA test doubles.

Hassfest and live HA separately validate the real integration registration/config flow.
"""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_provider


def load_transport():
    api = types.SimpleNamespace(websocket_command=lambda schema: lambda fn: fn, async_response=lambda fn: fn, async_register_command=Mock())
    vol = types.SimpleNamespace(Required=lambda key: key, All=lambda *args: args, Match=lambda pattern: pattern, In=lambda options: options)
    modules = {
        "voluptuous": vol,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.auth": types.ModuleType("homeassistant.auth"),
        "homeassistant.auth.permissions": types.ModuleType("homeassistant.auth.permissions"),
        "homeassistant.auth.permissions.const": types.SimpleNamespace(POLICY_READ="read"),
        "homeassistant.components": types.SimpleNamespace(websocket_api=api),
        "homeassistant.config_entries": types.SimpleNamespace(ConfigEntry=object),
        "homeassistant.core": types.SimpleNamespace(HomeAssistant=object),
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.aiohttp_client": types.SimpleNamespace(async_get_clientsession=lambda hass: hass.session),
    }
    spec = importlib.util.spec_from_file_location("stock_test.transport", Path(test_provider.package.__path__[0]) / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "stock_test"
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


transport = load_transport()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = types.SimpleNamespace(history=AsyncMock(return_value={"source": "example"}), close=AsyncMock())
        self.hass = types.SimpleNamespace(data={"amazing_stock_data": {"avanza": self.provider}}, states=types.SimpleNamespace(get=Mock(return_value=object())))
        self.connection = types.SimpleNamespace(user=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=Mock(return_value=True))), send_error=Mock(), send_result=Mock())
        self.msg = {"id": 1, "type": "amazing_stock_data/history", "provider": "avanza", "entity_id": "sensor.example", "instrument_id": "123", "period": "max"}

    async def test_permission_denial_performs_no_upstream_request(self):
        self.connection.user.permissions.check_entity.return_value = False
        await transport.ws_history(self.hass, self.connection, self.msg)
        self.connection.send_error.assert_called_once_with(1, "unauthorized", "Entity read permission required")
        self.provider.history.assert_not_awaited()

    async def test_missing_sensor_and_unloaded_integration(self):
        self.hass.states.get.return_value = None
        await transport.ws_history(self.hass, self.connection, self.msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "entity_not_found")
        self.hass.states.get.return_value = object()
        self.hass.data.clear()
        await transport.ws_history(self.hass, self.connection, self.msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_loaded")
        self.provider.history.assert_not_awaited()

    async def test_success_and_safe_error(self):
        await transport.ws_history(self.hass, self.connection, self.msg)
        self.provider.history.assert_awaited_once_with("123", "max")
        self.connection.send_result.assert_called_once_with(1, {"source": "example"})
        self.provider.history.side_effect = transport.HistoryError("rate_limited")
        await transport.ws_history(self.hass, self.connection, self.msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "rate_limited")

    async def test_unload_closes_provider_and_removes_access(self):
        self.assertTrue(await transport.async_unload_entry(self.hass, object()))
        self.provider.close.assert_awaited_once()
        self.assertNotIn("amazing_stock_data", self.hass.data)
