"""Regression tests for control/settings, using lightweight HA substitutes.

Run with aiohttp and voluptuous installed: python tests/test_review.py
These tests do not replace validation in a running Home Assistant instance.
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "hiveos_miner"


def module(name, **attributes):
    obj = types.ModuleType(name)
    obj.__dict__.update(attributes)
    sys.modules[name] = obj
    return obj


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass


class Coordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None

    async def async_request_refresh(self):
        self.data = await self._async_update_data()


class AuthFailed(Exception):
    pass


module("homeassistant")
module("homeassistant.config_entries", ConfigEntry=object, ConfigFlow=Flow,
       ConfigFlowResult=dict, OptionsFlow=Flow)
module("homeassistant.core", HomeAssistant=object)
module("homeassistant.exceptions", ConfigEntryAuthFailed=AuthFailed)
module("homeassistant.helpers")
module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=Coordinator,
       UpdateFailed=RuntimeError)
dt = module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))
module("homeassistant.util", dt=dt)
pkg = module("hiveos_miner")
pkg.__path__ = [str(ROOT)]


def load(name):
    spec = importlib.util.spec_from_file_location(f"hiveos_miner.{name}", ROOT / f"{name}.py")
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


load("const")
api = load("api")
flow = load("config_flow")
coordinator = load("coordinator")
options = load("options_flow")


class SettingsTests(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual(flow.normalise_host("http://MINER:8080/"), "miner:8080")
        self.assertEqual(flow.normalise_host("[::1]:8080"), "[::1]:8080")
        for address in ("", "https://miner", "miner/path", "user:pass@miner", "miner:99999"):
            with self.subTest(address=address), self.assertRaises(Exception):
                flow.normalise_host(address)

    def test_poll_interval(self):
        for interval in (-1, 0, 4, 3601):
            with self.subTest(interval=interval), self.assertRaises(Exception):
                flow.SCAN_INTERVAL_SCHEMA(interval)
        self.assertEqual(flow.SCAN_INTERVAL_SCHEMA(30), 30)

    def test_parser_guards(self):
        for payload in ([], {}, {"summary": []}):
            with self.assertRaises(api.HiveosMinerError):
                api.parse_miner_status(payload)
        parsed = api.parse_miner_status({"summary": {"ghs5s": "NaN"},
                                         "devs": [None], "pools": [None, {}]})
        self.assertIsNone(parsed["hashrate"])
        self.assertEqual(parsed["pools_total"], 0)

    def test_board_temperature_maximum(self):
        parsed = api.parse_miner_status({"summary": {}, "devs": [{"temp": 30}, {"temp": 80}]})
        self.assertEqual(parsed["temp_max"], 80)

    def test_options_factory(self):
        self.assertIsInstance(flow.HiveosMinerConfigFlow.async_get_options_flow(None),
                              options.HiveosMinerOptionsFlow)

    def test_state_precedence(self):
        self.assertEqual(api.derive_state(0, "SUSPENDED", True), "starting")
        self.assertEqual(api.derive_state(0, "SUSPENDED\nStarting"), "starting")
        self.assertEqual(api.derive_state(0, "Starting\nSUSPENDED"), "suspended")
        self.assertEqual(api.derive_state(0.01, "SUSPENDED"), "mining")


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = types.SimpleNamespace(data={"host": "miner"}, async_start_reauth=lambda hass: None)
        self.c = coordinator.HiveosMinerCoordinator(object(), self.entry)
        self.c.api = types.SimpleNamespace(
            async_get=AsyncMock(return_value={"summary": {"ghs5s": "0"}}),
            async_log_tail=AsyncMock(return_value=""),
            async_start=AsyncMock(), async_stop=AsyncMock(), async_resume=AsyncMock(),
        )

    async def test_concurrent_start_only_once(self):
        self.c.data = {"state": "stopped"}
        await asyncio.gather(self.c.async_set_mining(True), self.c.async_set_mining(True))
        self.c.api.async_start.assert_awaited_once()
        self.assertEqual(self.c.data["state"], "starting")

    async def test_pause_without_log_then_resume(self):
        self.c.data = {"state": "mining"}
        await self.c.async_set_mining(False)
        self.assertEqual(self.c.data["state"], "suspended")
        await self.c.async_set_mining(True)
        self.c.api.async_resume.assert_awaited_once()
        self.assertEqual(self.c.data["state"], "starting")

    async def test_running_start_is_noop(self):
        self.c.data = {"state": "mining"}
        await self.c.async_set_mining(True)
        self.c.api.async_start.assert_not_awaited()

    async def test_offline_and_auth_are_distinct(self):
        self.c.api.async_get.side_effect = api.HiveosMinerConnectionError("offline")
        data = await self.c._async_update_data()
        self.assertEqual(data["state"], "stopped")
        self.assertEqual(data["pools_alive"], 0)
        self.c.api.async_get.side_effect = api.HiveosMinerAuthError("unauthorized")
        with self.assertRaises(AuthFailed):
            await self.c._async_update_data()

    async def test_validation_rejects_wrong_server(self):
        client = api.HiveosMinerApi("miner")
        client.async_get = AsyncMock(return_value={"other": "service"})
        with self.assertRaises(api.HiveosMinerError):
            await client.async_validate()


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = types.SimpleNamespace(entry_id="entry", domain="hiveos_miner",
                                          data={"host": "miner", "name": "Old", "scan_interval": 30})
        self.manager = types.SimpleNamespace(
            async_get_entry=Mock(return_value=self.entry),
            async_entries=Mock(return_value=[self.entry]),
            async_update_entry=Mock(),
        )
        self.flow = options.HiveosMinerOptionsFlow()
        self.flow.hass = types.SimpleNamespace(config_entries=self.manager)
        self.flow.handler = "entry"
        self.flow.async_show_form = Mock(side_effect=lambda **kw: kw)
        self.flow.async_create_entry = Mock(side_effect=lambda **kw: kw)

    async def test_duplicate_address_does_not_save(self):
        self.manager.async_entries.return_value.append(
            types.SimpleNamespace(entry_id="other", data={"host": "other"})
        )
        result = await self.flow.async_step_init({"host": "other"})
        self.assertEqual(result["errors"], {"base": "already_configured"})
        self.manager.async_update_entry.assert_not_called()

    async def test_settings_update_title_and_keep_port(self):
        client = types.SimpleNamespace(async_validate=AsyncMock(return_value=True), close=AsyncMock())
        with patch.object(options, "HiveosMinerApi", return_value=client):
            await self.flow.async_step_init({"host": "http://miner:8080/", "name": "New"})
        kwargs = self.manager.async_update_entry.call_args.kwargs
        self.assertEqual(kwargs["title"], "New")
        self.assertEqual(kwargs["data"]["host"], "miner:8080")
        self.assertNotIn("options", kwargs)
        client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
