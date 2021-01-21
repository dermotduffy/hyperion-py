#!/usr/bin/python
"""Test for the Hyperion Client."""
import asyncio
import inspect
import json
import os
import unittest
import logging
import string
from typing import Any, Dict, List, Optional, Tuple

from asynctest import helpers, ClockedTestCase
from asynctest.mock import patch, call, Mock

from hyperion import client, const

logging.basicConfig()
_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

PATH_TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
TEST_HOST = "test"
TEST_PORT = 65000
TEST_TOKEN = "FAKE_TOKEN"
TEST_INSTANCE = 1

FILE_SERVERINFO_RESPONSE = "serverinfo_response_1.json"

SERVERINFO_REQUEST = {
    "command": "serverinfo",
    "subscribe": [
        "adjustment-update",
        "components-update",
        "effects-update",
        "leds-update",
        "imageToLedMapping-update",
        "instance-update",
        "priorities-update",
        "sessions-update",
        "videomode-update",
    ],
    "tan": 1,
}

TEST_SYSINFO_ID = "f9aab089-f85a-55cf-b7c1-222a72faebe9"
TEST_SYSINFO_VERSION = "2.0.0-alpha.8"
TEST_SYSINFO_RESPONSE = {
    "command": "sysinfo",
    "info": {
        "hyperion": {
            "build": "fix-request-tan (GitHub-78458e44/5d5b2497-1601058791)",
            "gitremote": "https://github.com/hyperion-project/hyperion.ng.git",
            "id": TEST_SYSINFO_ID,
            "time": "Sep 29 2020 12:33:00",
            "version": TEST_SYSINFO_VERSION,
        },
        "system": {
            "architecture": "arm",
            "domainName": "domain",
            "hostName": "hyperion",
            "kernelType": "linux",
            "kernelVersion": "5.4.51-v7l+",
            "prettyName": "Raspbian GNU/Linux 10 (buster)",
            "productType": "raspbian",
            "productVersion": "10",
            "wordSize": "32",
        },
    },
    "success": True,
}


def _get_test_filepath(filename: str) -> str:
    return os.path.join(PATH_TESTDATA, filename)


def _read_file(filename: str) -> Any:
    with open(_get_test_filepath(filename)) as handle:
        data = handle.read()
    return json.loads(data)


class MockStreamReaderWriter:
    """A simple mocl StreamReader and StreamWriter."""

    def __init__(self, flow: Optional[List[Tuple[str, Any]]] = None) -> None:
        """Initializse the mock."""
        self._flow = flow or []
        self._read_cv = asyncio.Condition()
        self._write_cv = asyncio.Condition()
        self._flow_cv = asyncio.Condition()
        self._data_to_drain: Optional[bytes] = None

    async def add_flow(self, flow: List[Tuple[str, Any]]) -> None:
        """Add expected calls to the flow."""
        async with self._flow_cv:
            self._flow.extend(flow)
        await self.unblock_read()
        await self.unblock_write()

    async def unblock_read(self) -> None:
        """Unblock the read call."""
        async with self._read_cv:
            self._read_cv.notify_all()

    async def unblock_write(self) -> None:
        """Unblock the write call."""
        async with self._write_cv:
            self._write_cv.notify_all()

    async def block_read(self) -> None:
        """Block the read call."""
        async with self._read_cv:
            await self._read_cv.wait()

    async def block_write(self) -> None:
        """Block the write call."""
        async with self._write_cv:
            await self._write_cv.wait()

    async def block_until_flow_empty(self) -> None:
        """Block until the flow has been consumed."""
        async with self._flow_cv:
            await self._flow_cv.wait_for(lambda: not self._flow)

    async def assert_flow_finished(self) -> None:
        """Assert that the flow has been consumed."""
        async with self._flow_cv:
            assert not self._flow

    @classmethod
    def _to_json_line(cls, data: Any) -> bytes:
        """Convert data to an encoded JSON string."""
        if isinstance(data, str):
            return data.encode("UTF-8")
        return (json.dumps(data, sort_keys=True) + "\n").encode("UTF-8")

    async def _pop_flow(self) -> Tuple[str, Any]:
        """Remove an item from the front of the flow and notify."""
        async with self._flow_cv:
            if not self._flow:
                _LOGGER.exception("Unexpected empty flow")
                raise AssertionError("Unexpected empty flow")
            item = self._flow.pop(0)
            self._flow_cv.notify_all()
            return item

    async def readline(self) -> bytes:
        """Read a line from the mock.

        Will block indefinitely if no read call is available.
        """

        _LOGGER.debug("Readline..")
        while True:
            should_block = False
            async with self._flow_cv:
                if not self._flow:
                    should_block = True
                else:
                    cmd = self._flow[0][0]

                    if cmd != "read":
                        should_block = True

            if should_block:
                await self.block_read()
                continue

            cmd, data = await self._pop_flow()
            await self.unblock_write()

            if isinstance(data, Exception):
                raise data
            return MockStreamReaderWriter._to_json_line(data)

    def close(self) -> None:
        """Close the mock."""

    async def wait_closed(self) -> None:
        """Wait for the close to complete."""
        _LOGGER.debug("MockStreamReaderWriter: wait_closed()")

        cmd, data = await self._pop_flow()
        assert cmd == "close", "wait_closed() called unexpectedly"

        if isinstance(data, Exception):
            raise data

    def write(self, data_in: bytes) -> None:
        """Write data to the mock."""
        _LOGGER.debug("MockStreamReaderWriter: write(%s)", data_in)
        assert self._data_to_drain is None
        self._data_to_drain = data_in

    async def drain(self) -> None:
        """Drain the most recent write to the mock.

        Will block if the next write in the flow (not necessarily the next call in
        the flow) matches that which is expected.
        """
        _LOGGER.debug("MockStreamReaderWriter: drain()")

        while True:
            assert self._data_to_drain is not None

            async with self._flow_cv:
                assert (
                    len(self._flow) > 0
                ), f"drain() called unexpectedly: {self._data_to_drain!r}"
                cmd, data = self._flow[0]

            should_block = False
            if cmd != "write":
                async with self._flow_cv:
                    for cmd_i, data_i in self._flow[1:]:
                        if cmd_i == "write":
                            assert json.loads(self._data_to_drain) == data_i
                            should_block = True
                            break
                    else:
                        raise AssertionError(
                            f"Unexpected call to drain with data "
                            f'"{self._data_to_drain!r}", expected "{cmd}" with data '
                            f'"{data!r}"'
                        )

            if should_block:
                await self.block_write()
                continue

            assert self._data_to_drain is not None

            if isinstance(data, Exception):
                # 'data' is an exception, raise it.
                await self._pop_flow()
                raise data
            if callable(data):
                # 'data' is a callable, call it with the decoded data.
                try:
                    data_in = json.loads(self._data_to_drain)
                except json.decoder.JSONDecodeError:
                    data_in = self._data_to_drain
                assert data(data_in)
            else:
                # 'data' is just data. Direct compare.
                assert self._data_to_drain == MockStreamReaderWriter._to_json_line(data)

            self._data_to_drain = None

            await self._pop_flow()
            await self.unblock_read()
            break


# Typing: asynctest does not expose type hints.
class AsyncHyperionClientTestCase(ClockedTestCase):  # type: ignore[misc]
    """Test case for the Hyperion Client."""

    def setUp(self) -> None:
        """Set up testcase."""
        self.loop.set_debug(enabled=True)
        client._LOGGER.setLevel(logging.DEBUG)  # pylint: disable=protected-access

    def tearDown(self) -> None:
        """Tear down testcase."""

    async def _create_and_test_basic_connected_client(
        self, **kwargs: Any
    ) -> Tuple[MockStreamReaderWriter, client.HyperionClient]:
        """Create a basic connected client object."""
        rw = MockStreamReaderWriter(
            [
                ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
                ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 1}}),
            ]
        )

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, **kwargs)
            self.assertTrue(await hc.async_client_connect())

        await rw.assert_flow_finished()
        return (rw, hc)

    async def _block_until_done(
        self, rw: MockStreamReaderWriter, additional_wait_secs: Optional[float] = None
    ) -> None:
        if additional_wait_secs:
            await self.advance(additional_wait_secs)
        await rw.block_until_flow_empty()
        await helpers.exhaust_callbacks(self.loop)

    async def _disconnect_and_assert_finished(
        self, rw: MockStreamReaderWriter, hc: client.HyperionClient
    ) -> None:
        await rw.add_flow([("close", None)])
        self.assertTrue(await hc.async_client_disconnect())
        await self._block_until_done(rw)
        self.assertFalse(hc.is_connected)
        await rw.assert_flow_finished()

    async def test_async_client_connect(self) -> None:
        """Test async connection to server."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        self.assertTrue(hc.is_connected)
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_client_connect_failure(self) -> None:
        """Test failed connection to server."""

        authorize_request = {
            "command": "authorize",
            "subcommand": "login",
            "token": TEST_TOKEN,
        }
        authorize_response = {"command": "authorize-login", "success": False}

        rw = MockStreamReaderWriter(
            [
                ("write", {**authorize_request, **{"tan": 1}}),
                ("read", {**authorize_response, **{"tan": 1}}),
                ("close", None),
            ]
        )

        # == Try to connect when the token fails.
        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, token=TEST_TOKEN)
            self.assertFalse(await hc.async_client_connect())
            self.assertFalse(hc.is_connected)
            await rw.assert_flow_finished()

        instance_request = {
            "command": "instance",
            "instance": TEST_INSTANCE,
            "subcommand": "switchTo",
        }
        instance_response = {
            "command": "instance-switchTo",
            "success": False,
            "info": {"instance": TEST_INSTANCE},
        }

        rw = MockStreamReaderWriter(
            [
                ("write", {**instance_request, **{"tan": 1}}),
                ("read", {**instance_response, **{"tan": 1}}),
                ("close", None),
            ]
        )

        # == Try to connect when the instance selection fails.
        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, instance=TEST_INSTANCE)
            self.assertFalse(await hc.async_client_connect())
            self.assertFalse(hc.is_connected)
            await rw.assert_flow_finished()

        rw = MockStreamReaderWriter(
            [
                ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
                (
                    "read",
                    {
                        **_read_file(FILE_SERVERINFO_RESPONSE),
                        **{"tan": 1, "success": False},
                    },
                ),
                ("close", None),
            ]
        )

        # == Try to connect when the serverinfo (state load) call fails.
        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT)
            self.assertFalse(await hc.async_client_connect())
            self.assertFalse(hc.is_connected)
            await rw.assert_flow_finished()

    async def test_async_client_connect_authorized(self) -> None:
        """Test server connection with authorization."""

        authorize_request = {
            "command": "authorize",
            "subcommand": "login",
            "token": TEST_TOKEN,
        }
        authorize_response = {"command": "authorize-login", "success": True}

        rw = MockStreamReaderWriter(
            [
                ("write", {**authorize_request, **{"tan": 1}}),
                ("read", {**authorize_response, **{"tan": 1}}),
                ("write", {**SERVERINFO_REQUEST, **{"tan": 2}}),
                ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 2}}),
            ]
        )

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, token=TEST_TOKEN)
            self.assertTrue(await hc.async_client_connect())
            self.assertTrue(hc.has_loaded_state)
            self.assertTrue(hc.is_logged_in)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_client_connect_specified_instance(self) -> None:
        """Test server connection to specified instance."""
        instance_request = {
            "command": "instance",
            "instance": TEST_INSTANCE,
            "subcommand": "switchTo",
        }
        instance_response = {
            "command": "instance-switchTo",
            "success": True,
            "info": {"instance": TEST_INSTANCE},
        }

        rw = MockStreamReaderWriter(
            [
                ("write", {**instance_request, **{"tan": 1}}),
                ("read", {**instance_response, **{"tan": 1}}),
                ("write", {**SERVERINFO_REQUEST, **{"tan": 2}}),
                ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 2}}),
            ]
        )

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, instance=TEST_INSTANCE)
            self.assertTrue(await hc.async_client_connect())

        self.assertEqual(hc.instance, TEST_INSTANCE)
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_client_connect_raw(self) -> None:
        """Test a raw connection."""
        rw = MockStreamReaderWriter()

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(
                TEST_HOST,
                TEST_PORT,
                instance=TEST_INSTANCE,
                token=TEST_TOKEN,
                raw_connection=True,
            )
            self.assertTrue(await hc.async_client_connect())

        # It's a raw connection, it will not be logged in, nor instance selected.
        self.assertTrue(hc.is_connected)
        self.assertFalse(hc.is_logged_in)
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)
        self.assertFalse(hc.has_loaded_state)

        # Manually log in.
        auth_login_in = {
            "command": "authorize",
            "subcommand": "login",
            "token": TEST_TOKEN,
            "tan": 1,
        }
        auth_login_out = {"command": "authorize-login", "success": True, "tan": 1}

        await rw.add_flow([("write", auth_login_in), ("read", auth_login_out)])
        self.assertTrue(await hc.async_client_login())

        # Manually switch instance (and get serverinfo automatically).
        switch_in = {
            "command": "instance",
            "subcommand": "switchTo",
            "instance": TEST_INSTANCE,
            "tan": 2,
        }
        switch_out = {
            "command": "instance-switchTo",
            "info": {"instance": TEST_INSTANCE},
            "success": True,
            "tan": 2,
        }
        await rw.add_flow(
            [
                ("write", switch_in),
                ("read", switch_out),
                ("write", {**SERVERINFO_REQUEST, **{"tan": 3}}),
                ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 3}}),
            ]
        )

        self.assertTrue(await hc.async_client_switch_instance())
        self.assertTrue(await hc.async_get_serverinfo())
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_instance_switch_causes_empty_state(
        self,
    ) -> None:
        """Test that an instance will have no state after an instance switch."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)

        instance = 1
        instance_switchto_request = {
            "command": "instance",
            "subcommand": "switchTo",
            "instance": instance,
        }
        instance_switchto_response = {
            "command": "instance-switchTo",
            "info": {"instance": instance},
            "success": True,
        }

        await rw.add_flow(
            [
                ("write", instance_switchto_request),
                ("read", instance_switchto_response),
            ]
        )

        self.assertTrue(await hc.async_send_switch_instance(instance=instance))
        await rw.block_until_flow_empty()

        self.assertTrue(hc.is_connected)
        self.assertEqual(hc.instance, instance)
        self.assertEqual(hc.target_instance, instance)
        self.assertFalse(hc.has_loaded_state)

        await self._disconnect_and_assert_finished(rw, hc)

        # Ensure there is no live instance, but that the target instance is the
        # one that was switched to.
        self.assertEqual(hc.instance, None)
        self.assertEqual(hc.target_instance, instance)

    async def test_receive_wrong_data_type(self) -> None:
        """Test that receiving the wrong data-type is handled."""
        rw = MockStreamReaderWriter(
            [
                ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
            ]
        )
        with patch("asyncio.open_connection", return_value=(rw, rw)):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, raw_connection=True)
            self.assertTrue(await hc.async_client_connect())

            task = asyncio.create_task(hc.async_get_serverinfo())
            await rw.block_until_flow_empty()
            await rw.add_flow(
                [
                    ("read", ["this", "is", "not", "a", "dict"]),
                ]
            )

            # Advance the clock so it times out waiting.
            await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)
            self.assertFalse(await task)
            await self._disconnect_and_assert_finished(rw, hc)

    async def test_is_on(self) -> None:
        """Test the client reports correctly on whether components are on."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        with open(_get_test_filepath(FILE_SERVERINFO_RESPONSE)) as fh:
            serverinfo_command_response = fh.readline()
        serverinfo = json.loads(serverinfo_command_response)

        # Verify server info is as expected.
        self.assertEqual(hc.serverinfo, serverinfo[const.KEY_INFO])

        # Verify the individual components.
        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_ALL]))
        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))
        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_BLACKBORDER]))
        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_FORWARDER]))
        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_BOBLIGHTSERVER]))
        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_GRABBER]))
        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_V4L]))
        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_LEDDEVICE]))

        # Verify combinations.
        self.assertTrue(
            hc.is_on(
                components=[
                    const.KEY_COMPONENTID_ALL,
                    const.KEY_COMPONENTID_SMOOTHING,
                    const.KEY_COMPONENTID_BLACKBORDER,
                ]
            )
        )
        self.assertFalse(
            hc.is_on(
                components=[
                    const.KEY_COMPONENTID_ALL,
                    const.KEY_COMPONENTID_GRABBER,
                    const.KEY_COMPONENTID_BLACKBORDER,
                ]
            )
        )

        # Verify default.
        self.assertTrue(hc.is_on())
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_component(self) -> None:
        """Test updating components."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        # === Verify flipping a component.
        components_update = {
            "command": "components-update",
            "data": {"enabled": False, "name": "SMOOTHING"},
        }

        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))

        await rw.add_flow([("read", components_update)])
        await self._block_until_done(rw)

        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))

        # === Verify a component change where the component name is not existing.
        component_name = "NOT_EXISTING"
        components_update = {
            "command": "components-update",
            "data": {"enabled": True, "name": component_name},
        }

        self.assertFalse(hc.is_on(components=[component_name]))

        await rw.add_flow([("read", components_update)])
        await self._block_until_done(rw)

        self.assertTrue(hc.is_on(components=[component_name]))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_adjustment(self) -> None:
        """Test updating adjustments."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        adjustment_update = {
            "command": "adjustment-update",
            "data": [{"brightness": 25}],
        }
        assert hc.adjustment
        self.assertEqual(hc.adjustment[0]["brightness"], 83)

        await rw.add_flow([("read", adjustment_update)])
        await self._block_until_done(rw)

        self.assertEqual(hc.adjustment[0]["brightness"], 25)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_effect_list(self) -> None:
        """Test updating effect list."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        effect = {
            "args": {
                "hueChange": 60,
                "reverse": False,
                "rotationTime": 60,
                "smoothing-custom-settings": None,
            },
            "file": ":/effects//mood-blobs-blue.json",
            "name": "Blue mood blobs",
            "script": ":/effects//mood-blobs.py",
        }

        effects_update = {
            "command": "effects-update",
            "data": [effect],
        }

        await rw.add_flow([("read", effects_update)])
        await self._block_until_done(rw)

        assert hc.effects
        self.assertEqual(len(hc.effects), 1)
        self.assertEqual(hc.effects[0], effect)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_priorities(self) -> None:
        """Test updating priorities."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        priorities = [
            {
                "active": True,
                "componentId": "GRABBER",
                "origin": "System",
                "owner": "X11",
                "priority": 250,
                "visible": True,
            },
            {
                "active": True,
                "componentId": "EFFECT",
                "origin": "System",
                "owner": "Warm mood blobs",
                "priority": 254,
                "visible": False,
            },
            {
                "active": True,
                "componentId": "COLOR",
                "origin": "System",
                "owner": "System",
                "priority": 40,
                "value": {"HSL": [65535, 0, 0], "RGB": [0, 0, 0]},
                "visible": False,
            },
        ]
        priorities_update = {
            "command": "priorities-update",
            "data": {"priorities": priorities, "priorities_autoselect": False},
        }

        assert hc.priorities
        self.assertEqual(len(hc.priorities), 2)
        self.assertTrue(hc.priorities_autoselect)
        assert hc.visible_priority
        self.assertEqual(hc.visible_priority["priority"], 240)

        await rw.add_flow([("read", priorities_update)])
        await self._block_until_done(rw)

        self.assertEqual(hc.priorities, priorities)
        self.assertEqual(hc.visible_priority, priorities[0])
        self.assertFalse(hc.priorities_autoselect)

        priorities_update = {
            "command": "priorities-update",
            "data": {"priorities": [], "priorities_autoselect": True},
        }

        await rw.add_flow([("read", priorities_update)])
        await self._block_until_done(rw)

        self.assertIsNone(hc.visible_priority)
        self.assertTrue(hc.priorities_autoselect)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_instances(self) -> None:
        """Test updating instances."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        assert hc.instances
        self.assertEqual(len(hc.instances), 2)
        self.assertEqual(hc.instance, 0)
        self.assertEqual(hc.target_instance, 0)

        instances = [
            {"instance": 0, "running": True, "friendly_name": "Test instance 0"},
            {"instance": 1, "running": True, "friendly_name": "Test instance 1"},
            {"instance": 2, "running": True, "friendly_name": "Test instance 2"},
        ]

        instances_update = {
            "command": "instance-update",
            "data": instances,
        }

        await rw.add_flow([("read", instances_update)])
        await self._block_until_done(rw)
        self.assertEqual(hc.instances, instances)

        # Now update instances again to exclude instance 1 (it should reset to 0).
        instances = [
            {"instance": 0, "running": False, "friendly_name": "Test instance 0"},
            {"instance": 1, "running": True, "friendly_name": "Test instance 1"},
            {"instance": 2, "running": True, "friendly_name": "Test instance 2"},
        ]

        instances_update = {
            "command": "instance-update",
            "data": instances,
        }

        await rw.add_flow(
            [
                ("read", instances_update),
                ("close", None),
            ]
        )
        await self._block_until_done(rw)
        self.assertFalse(hc.is_connected)
        self.assertEqual(hc.instance, None)
        self.assertEqual(hc.target_instance, 0)
        await rw.assert_flow_finished()

    async def test_update_led_mapping_type(self) -> None:
        """Test updating LED mapping type."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        led_mapping_type = "unicolor_mean"

        led_mapping_type_update = {
            "command": "imageToLedMapping-update",
            "data": {"imageToLedMappingType": led_mapping_type},
        }

        self.assertNotEqual(hc.led_mapping_type, led_mapping_type)
        await rw.add_flow([("read", led_mapping_type_update)])
        await self._block_until_done(rw)
        self.assertEqual(hc.led_mapping_type, led_mapping_type)
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_sessions(self) -> None:
        """Test updating sessions."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        sessions = [
            {
                "address": "192.168.58.169",
                "domain": "local.",
                "host": "ubuntu-2",
                "name": "My Hyperion Config@ubuntu:8090",
                "port": 8090,
                "type": "_hyperiond-http._tcp.",
            }
        ]

        sessions_update = {
            "command": "sessions-update",
            "data": sessions,
        }

        self.assertEqual(hc.sessions, [])
        await rw.add_flow([("read", sessions_update)])
        await self._block_until_done(rw)
        self.assertEqual(hc.sessions, sessions)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_videomode(self) -> None:
        """Test updating videomode."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        videomode = "3DSBS"

        videomode_update = {
            "command": "videomode-update",
            "data": {"videomode": videomode},
        }

        self.assertEqual(hc.videomode, "2D")
        await rw.add_flow([("read", videomode_update)])
        await self._block_until_done(rw)
        self.assertEqual(hc.videomode, videomode)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_update_leds(self) -> None:
        """Test updating LEDs."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        leds = [{"hmin": 0.0, "hmax": 1.0, "vmin": 0.0, "vmax": 1.0}]
        leds_update = {"command": "leds-update", "data": {"leds": leds}}

        assert hc.leds
        self.assertEqual(len(hc.leds), 254)
        await rw.add_flow([("read", leds_update)])
        await self._block_until_done(rw)
        self.assertEqual(hc.leds, leds)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_color(self) -> None:
        """Test controlling color."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        color_in = {
            "color": [0, 0, 255],
            "command": "color",
            "origin": "My Fancy App",
            "priority": 50,
        }

        await rw.add_flow([("write", color_in)])
        self.assertTrue(await hc.async_send_set_color(**color_in))
        await self._block_until_done(rw)

        color_in = {
            "color": [0, 0, 255],
            "priority": 50,
        }
        color_out = {
            "command": "color",
            "color": [0, 0, 255],
            "priority": 50,
            "origin": const.DEFAULT_ORIGIN,
        }

        await rw.add_flow([("write", color_out)])
        self.assertTrue(await hc.async_send_set_color(**color_in))
        await self._block_until_done(rw)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_effect(self) -> None:
        """Test controlling effect."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        effect_in = {
            "command": "effect",
            "effect": {"name": "Warm mood blobs"},
            "priority": 50,
            "origin": "My Fancy App",
        }

        await rw.add_flow([("write", effect_in)])
        self.assertTrue(await hc.async_send_set_effect(**effect_in))

        effect_in = {
            "effect": {"name": "Warm mood blobs"},
            "priority": 50,
        }
        effect_out = {
            "command": "effect",
            "effect": {"name": "Warm mood blobs"},
            "priority": 50,
            "origin": const.DEFAULT_ORIGIN,
        }

        await rw.add_flow([("write", effect_out)])
        self.assertTrue(await hc.async_send_set_effect(**effect_in))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_image(self) -> None:
        """Test controlling image."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        image_in = {
            "command": "image",
            "imagedata": "VGhpcyBpcyBubyBpbWFnZSEgOik=",
            "name": "Name of Image",
            "format": "auto",
            "priority": 50,
            "duration": 5000,
            "origin": "My Fancy App",
        }

        await rw.add_flow([("write", image_in)])
        self.assertTrue(await hc.async_send_set_image(**image_in))

        image_in = {
            "imagedata": "VGhpcyBpcyBubyBpbWFnZSEgOik=",
            "name": "Name of Image",
            "format": "auto",
            "priority": 50,
            "duration": 5000,
        }

        image_out = {
            "command": "image",
            "imagedata": "VGhpcyBpcyBubyBpbWFnZSEgOik=",
            "name": "Name of Image",
            "format": "auto",
            "priority": 50,
            "duration": 5000,
            "origin": const.DEFAULT_ORIGIN,
        }

        await rw.add_flow([("write", image_out)])
        self.assertTrue(await hc.async_send_set_image(**image_in))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_clear(self) -> None:
        """Test clearing priorities."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        clear_in = {
            "command": "clear",
            "priority": 50,
        }

        await rw.add_flow([("write", clear_in)])
        self.assertTrue(await hc.async_send_clear(**clear_in))
        await rw.add_flow([("write", clear_in)])
        self.assertTrue(await hc.async_send_clear(priority=50))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_adjustment(self) -> None:
        """Test setting adjustment."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        adjustment_in = {"command": "adjustment", "adjustment": {"gammaRed": 1.5}}

        await rw.add_flow([("write", adjustment_in)])
        self.assertTrue(await hc.async_send_set_adjustment(**adjustment_in))
        await rw.add_flow([("write", adjustment_in)])
        self.assertTrue(
            await hc.async_send_set_adjustment(adjustment={"gammaRed": 1.5})
        )

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_led_mapping_type(self) -> None:
        """Test setting adjustment."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        led_mapping_type_in = {
            "command": "processing",
            "mappingType": "multicolor_mean",
        }

        await rw.add_flow([("write", led_mapping_type_in)])
        self.assertTrue(await hc.async_send_set_led_mapping_type(**led_mapping_type_in))
        await rw.add_flow([("write", led_mapping_type_in)])
        self.assertTrue(
            await hc.async_send_set_led_mapping_type(mappingType="multicolor_mean")
        )

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_videomode(self) -> None:
        """Test setting videomode."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        videomode_in = {"command": "videomode", "videoMode": "3DTAB"}

        await rw.add_flow([("write", videomode_in)])
        self.assertTrue(await hc.async_send_set_videomode(**videomode_in))
        await rw.add_flow([("write", videomode_in)])
        self.assertTrue(await hc.async_send_set_videomode(videoMode="3DTAB"))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_component(self) -> None:
        """Test setting component."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        componentstate = {
            "component": "LEDDEVICE",
            "state": False,
        }
        component_in = {
            "command": "componentstate",
            "componentstate": componentstate,
        }

        await rw.add_flow([("write", component_in)])
        self.assertTrue(await hc.async_send_set_component(**component_in))
        await rw.add_flow([("write", component_in)])
        self.assertTrue(
            await hc.async_send_set_component(componentstate=componentstate)
        )

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_set_sourceselect(self) -> None:
        """Test setting sourceselect."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        sourceselect_in = {"command": "sourceselect", "priority": 50}

        await rw.add_flow([("write", sourceselect_in)])
        self.assertTrue(await hc.async_send_set_sourceselect(**sourceselect_in))
        await rw.add_flow([("write", sourceselect_in)])
        self.assertTrue(await hc.async_send_set_sourceselect(priority=50))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_start_async_send_stop_switch_instance(self) -> None:
        """Test starting, stopping and switching instances."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "instance", "subcommand": "startInstance", "instance": 1}

        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_start_instance(**start_in))
        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_start_instance(instance=1))

        stop_in = {"command": "instance", "subcommand": "stopInstance", "instance": 1}

        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_stop_instance(**stop_in))
        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_stop_instance(instance=1))

        switch_in = {"command": "instance", "subcommand": "switchTo", "instance": 1}

        await rw.add_flow([("write", switch_in)])
        self.assertTrue(await hc.async_send_switch_instance(**switch_in))
        await rw.add_flow([("write", switch_in)])
        self.assertTrue(await hc.async_send_switch_instance(instance=1))

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_start_async_send_stop_image_stream(self) -> None:
        """Test starting and stopping an image stream."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "ledcolors", "subcommand": "imagestream-start"}

        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_image_stream_start(**start_in))
        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_image_stream_start())

        stop_in = {"command": "ledcolors", "subcommand": "imagestream-stop"}

        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_image_stream_stop(**stop_in))
        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_image_stream_stop())

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_start_stop_led_stream(self) -> None:
        """Test starting and stopping an led stream."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "ledcolors", "subcommand": "ledstream-start"}

        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_led_stream_start(**start_in))
        await rw.add_flow([("write", start_in)])
        self.assertTrue(await hc.async_send_led_stream_start())

        stop_in = {"command": "ledcolors", "subcommand": "ledstream-stop"}

        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_led_stream_stop(**stop_in))
        await rw.add_flow([("write", stop_in)])
        self.assertTrue(await hc.async_send_led_stream_stop())

        await self._disconnect_and_assert_finished(rw, hc)

    # pylint: disable=too-many-statements
    async def test_callbacks(self) -> None:
        """Test updating components."""
        cb = Mock()

        (rw, hc) = await self._create_and_test_basic_connected_client(
            default_callback=cb.default_callback,
            callbacks={
                "components-update": cb.component_callback,
                "serverinfo": cb.serverinfo_callback,
                "client-update": cb.client_callback,
            },
        )

        self.assertEqual(
            [
                call(
                    {
                        "command": "client-update",
                        "connected": True,
                        "logged-in": False,
                        "instance": const.DEFAULT_INSTANCE,
                        "loaded-state": False,
                    }
                ),
                call(
                    {
                        "command": "client-update",
                        "connected": True,
                        "logged-in": True,
                        "instance": const.DEFAULT_INSTANCE,
                        "loaded-state": False,
                    }
                ),
                call(
                    {
                        "command": "client-update",
                        "connected": True,
                        "logged-in": True,
                        "instance": const.DEFAULT_INSTANCE,
                        "loaded-state": True,
                    }
                ),
            ],
            cb.client_callback.call_args_list,
        )

        self.assertEqual(
            cb.serverinfo_callback.call_args[0][0],
            _read_file(FILE_SERVERINFO_RESPONSE),
        )
        cb.reset_mock()

        # === Flip a component.
        components_update = {
            "command": "components-update",
            "data": {"enabled": False, "name": "SMOOTHING"},
        }

        # Make sure the callback was called.
        await rw.add_flow([("read", components_update)])
        await self._block_until_done(rw)

        cb.default_callback.assert_not_called()
        cb.component_callback.assert_called_once_with(components_update)
        cb.reset_mock()

        # Call with a new update that does not have a registered callback.
        random_update_value = "random-update"
        random_update = {
            "command": random_update_value,
        }
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)

        cb.default_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Now set a callback for that update.
        hc.set_callbacks({random_update_value: cb.first_callback})
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Now add a second callback for that update.
        hc.add_callbacks({random_update_value: cb.second_callback})
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_called_once_with(random_update)
        cb.second_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Now add multiple callbacks.
        hc.add_callbacks({random_update_value: [cb.third_callback, cb.fourth_callback]})
        hc.add_callbacks({})
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_called_once_with(random_update)
        cb.second_callback.assert_called_once_with(random_update)
        cb.third_callback.assert_called_once_with(random_update)
        cb.fourth_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Set multiple callbacks (effectively removing  a few).
        hc.set_callbacks({random_update_value: [cb.third_callback, cb.fourth_callback]})
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_not_called()
        cb.second_callback.assert_not_called()
        cb.third_callback.assert_called_once_with(random_update)
        cb.fourth_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Remove some callbacks.
        hc.add_callbacks({random_update_value: [cb.first_callback, cb.second_callback]})
        hc.remove_callbacks({random_update_value: cb.third_callback})
        hc.remove_callbacks({random_update_value: [cb.fourth_callback]})
        hc.remove_callbacks({})
        hc.remove_callbacks({"not-here": cb.null_callback})
        hc.remove_callbacks({random_update_value: []})
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_called_once_with(random_update)
        cb.second_callback.assert_called_once_with(random_update)
        cb.third_callback.assert_not_called()
        cb.fourth_callback.assert_not_called()
        cb.null_callback.assert_not_called()
        cb.reset_mock()

        # Remove all callbacks.
        hc.set_callbacks(None)
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.first_callback.assert_not_called()
        cb.second_callback.assert_not_called()
        cb.reset_mock()

        # Add another default callback.
        hc.add_default_callback(cb.second_default_callback)
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.default_callback.assert_called_once_with(random_update)
        cb.second_default_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        # Remove a default callback.
        hc.remove_default_callback(cb.default_callback)
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.default_callback.assert_not_called()
        cb.second_default_callback.assert_called_once_with(random_update)
        cb.reset_mock()

        awaitable_json = None

        async def awaitable_callback(arg: Dict[str, Any]) -> None:
            nonlocal awaitable_json
            awaitable_json = arg

        # Set an async default callback.
        hc.set_default_callback(awaitable_callback)
        await rw.add_flow([("read", random_update)])
        await self._block_until_done(rw)
        cb.default_callback.assert_not_called()
        cb.second_default_callback.assert_not_called()
        self.assertEqual(awaitable_json, random_update)

        # Verify disconnection callback.
        hc.set_callbacks({"client-update": cb.client_callback})
        await self._disconnect_and_assert_finished(rw, hc)

        cb.client_callback.assert_called_once_with(
            {
                "command": "client-update",
                "connected": False,
                "instance": None,
                "loaded-state": False,
                "logged-in": False,
            },
        )

    async def test_is_auth_required(self) -> None:
        """Test determining if authorization is required."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        auth_request = {"command": "authorize", "subcommand": "tokenRequired", "tan": 2}

        auth_response = {
            "command": "authorize-tokenRequired",
            "info": {"required": True},
            "success": True,
            "tan": 2,
        }

        await rw.add_flow([("write", auth_request), ("read", auth_response)])
        received = await hc.async_is_auth_required()
        await self._block_until_done(rw)
        self.assertEqual(received, auth_response)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_login(self) -> None:
        """Test setting videomode."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        token = "sekrit"
        auth_login_in = {
            "command": "authorize",
            "subcommand": "login",
            "token": token,
        }

        await rw.add_flow([("write", auth_login_in)])
        self.assertTrue(await hc.async_send_login(**auth_login_in))
        await rw.add_flow([("write", auth_login_in)])
        self.assertTrue(await hc.async_send_login(token=token))
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_logout(self) -> None:
        """Test setting videomode."""
        before_tasks = asyncio.all_tasks()
        (rw, hc) = await self._create_and_test_basic_connected_client()
        auth_logout_in = {
            "command": "authorize",
            "subcommand": "logout",
        }

        await rw.add_flow([("write", auth_logout_in)])
        self.assertTrue(await hc.async_send_logout(**auth_logout_in))
        await rw.add_flow([("write", auth_logout_in)])
        self.assertTrue(await hc.async_send_logout())

        # A logout success response should cause the client to disconnect.
        auth_logout_out = {
            "command": "authorize-logout",
            "success": True,
        }

        await rw.add_flow([("read", auth_logout_out), ("close", None)])

        await self._block_until_done(rw)
        self.assertFalse(hc.is_connected)
        await rw.assert_flow_finished()

        # Verify there are no tasks left running (logout is interesting
        # in that the disconnection is from the receive task, so cancellation
        # of the receive task could cut the disconnection process off).
        after_tasks = asyncio.all_tasks()

        self.assertEqual(before_tasks, after_tasks)

    async def test_async_send_request_token(self) -> None:
        """Test requesting an auth token."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        # Test requesting a token.
        request_token_in: Dict[str, Any] = {
            "command": "authorize",
            "subcommand": "requestToken",
            "comment": "Test",
            "id": "T3c92",
        }

        await rw.add_flow([("write", request_token_in)])
        self.assertTrue(await hc.async_send_request_token(**request_token_in))

        # Test requesting a token with minimal provided parameters, will cause
        # the ID to be automatically generated.
        small_request_token_in = {
            "comment": "Test",
        }

        # Ensure an ID gets generated.
        await rw.add_flow(
            [
                (
                    "write",
                    lambda x: (
                        len(x.get("id")) == 5
                        and [x.get(key) for key in ["command", "subcommand", "comment"]]
                        == [
                            request_token_in.get(key)
                            for key in ["command", "subcommand", "comment"]
                        ]
                    ),
                )
            ]
        )

        self.assertTrue(await hc.async_send_request_token(**small_request_token_in))

        # Abort a request for a token.
        request_token_in["accept"] = False
        await rw.add_flow([("write", request_token_in)])
        self.assertTrue(await hc.async_send_request_token_abort(**request_token_in))
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_serverinfo(self) -> None:
        """Test requesting serverinfo."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        await rw.add_flow([("write", SERVERINFO_REQUEST)])
        self.assertTrue(await hc.async_send_get_serverinfo(**SERVERINFO_REQUEST))
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_disconnecting_leaves_no_tasks(self) -> None:
        """Verify stopping the background task."""
        before_tasks = asyncio.all_tasks()
        (rw, hc) = await self._create_and_test_basic_connected_client()
        await self._disconnect_and_assert_finished(rw, hc)
        after_tasks = asyncio.all_tasks()
        self.assertEqual(before_tasks, after_tasks)

    async def test_threaded_client(self) -> None:
        """Test the threaded client."""

        hc = client.ThreadedHyperionClient(
            TEST_HOST,
            TEST_PORT,
        )

        # Start the loop in the other thread.
        hc.start()
        hc.wait_for_client_init()

        # Note: MockStreamReaderWriter is not thread safe, so only a very limited test
        # is performed here.
        with patch("asyncio.open_connection", side_effect=ConnectionError):
            self.assertFalse(hc.client_connect())
            self.assertFalse(hc.is_connected)

        hc.stop()
        hc.join()

    def test_threaded_client_has_correct_methods(self) -> None:
        """Verify the threaded client exports all the correct methods."""
        contents = dir(
            client.ThreadedHyperionClient(
                TEST_HOST,
                TEST_PORT,
            )
        )

        # Verify all async methods have a sync wrapped version.
        for name, _ in inspect.getmembers(
            client.ThreadedHyperionClient, inspect.iscoroutinefunction
        ):
            if name.startswith("async_"):
                self.assertIn(name[len("async_") :], contents)

        for name, _ in inspect.getmembers(
            client.ThreadedHyperionClient, lambda o: isinstance(o, property)
        ):
            self.assertIn(name, contents)

    async def test_client_write_and_close_handles_network_issues(self) -> None:
        """Verify sending data does not throw exceptions."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        # Verify none of these write operations result in an exception
        # propagating to the test.

        await rw.add_flow([("write", ConnectionError("Write exception"))])
        self.assertFalse(await hc.async_send_image_stream_start())

        await rw.add_flow([("close", ConnectionError("Close exception"))])
        self.assertFalse(await hc.async_client_disconnect())

        await rw.assert_flow_finished()

    async def test_client_handles_network_issues_and_reconnects(self) -> None:
        """Verify sending data does not throw exceptions."""

        # == Verify a read exception causes a disconnect.
        (rw, hc) = await self._create_and_test_basic_connected_client()

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            await rw.add_flow(
                [
                    ("read", ConnectionError("Read exception")),
                    ("close", None),
                    ("write", {**SERVERINFO_REQUEST, **{"tan": 2}}),
                    (
                        "read",
                        {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 2}},
                    ),
                ]
            )
            await self._block_until_done(rw)
            await self._disconnect_and_assert_finished(rw, hc)

        # == Verify an empty read causes a disconnect and reconnect.
        (rw, hc) = await self._create_and_test_basic_connected_client()

        # Stage 1: Read returns empty, connection closed, but instantly re-established.
        with patch("asyncio.open_connection", return_value=(rw, rw)):
            await rw.add_flow(
                [
                    ("read", ""),
                    ("close", None),
                    ("write", {**SERVERINFO_REQUEST, **{"tan": 2}}),
                    (
                        "read",
                        {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 2}},
                    ),
                ]
            )
            await self._block_until_done(rw)

        # Stage 2: Read throws an exception, connection closed, cannot be
        # re-established.
        with patch("asyncio.open_connection", side_effect=ConnectionError):
            await rw.add_flow(
                [("read", ConnectionError("Connect error")), ("close", None)]
            )
            await rw.block_until_flow_empty()

            # Stage 3: Wait f"{const.DEFAULT_CONNECTION_RETRY_DELAY_SECS}" seconds
            # Check at half that timeout that we're still not connected.
            self.assertFalse(hc.is_connected)
            await self.advance(const.DEFAULT_CONNECTION_RETRY_DELAY_SECS / 2)
            self.assertFalse(hc.is_connected)

            # Stage 4: Fast-forward the remaining half of the timeout (+1 to ensure
            # we're definitely on the far side of the timeout), and it should
            # automatically reload.
            with patch("asyncio.open_connection", return_value=(rw, rw)):
                await rw.add_flow(
                    [
                        ("write", {**SERVERINFO_REQUEST, **{"tan": 3}}),
                        (
                            "read",
                            {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 3}},
                        ),
                    ]
                )

                await self.advance((const.DEFAULT_CONNECTION_RETRY_DELAY_SECS / 2) + 1)
                await self._block_until_done(rw)

            await self._disconnect_and_assert_finished(rw, hc)

    async def test_client_connect_handles_network_issues(self) -> None:
        """Verify connecting does throw exceptions and behaves correctly."""

        (rw, hc) = await self._create_and_test_basic_connected_client()

        with patch(
            "asyncio.open_connection",
            side_effect=ConnectionError("Connection exception"),
        ):
            await rw.add_flow([("read", ""), ("close", None)])
            await rw.block_until_flow_empty()
            self.assertFalse(hc.is_connected)
            self.assertTrue(await hc.async_client_disconnect())
            await rw.assert_flow_finished()

    async def test_client_timeout(self) -> None:
        """Verify connection and read timeouts behave correctly."""

        # == Verify timeout is dealt with correctly during connection.
        with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT)
            self.assertFalse(await hc.async_client_connect())

        # == Verify timeout is dealt with during read.
        (rw, hc) = await self._create_and_test_basic_connected_client()

        await rw.add_flow([("write", {**SERVERINFO_REQUEST, **{"tan": 2}})])

        # Create a new task to get the serverinfo ...
        task = asyncio.create_task(hc.async_get_serverinfo())
        # ... wait until the flow is empty (i.e. the request is written to the
        # server).
        await rw.block_until_flow_empty()

        # Advance the clock so it times out waiting.
        await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)

        # Ensuring the task fails.
        self.assertFalse(await task)

        # == Verify custom timeouts function in calls.
        await rw.add_flow([("write", {**SERVERINFO_REQUEST, **{"tan": 3}})])

        # Create a task that fetches serverinfo and will wait 3 times the default.
        task = asyncio.create_task(
            hc.async_get_serverinfo(timeout_secs=const.DEFAULT_TIMEOUT_SECS * 3)
        )

        # Wait 2 times the default (task should NOT have timed out)
        await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)

        self.assertFalse(task.done())

        # Wait a further two times (should have timed out)
        await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)
        self.assertTrue(task.done())
        self.assertFalse(await task)

        # == Verify request_token has a much larger default timeout.
        auth_id = ("T3c92",)
        comment = const.DEFAULT_ORIGIN
        request_token_in = {
            "command": "authorize",
            "subcommand": "requestToken",
            "comment": comment,
            "id": auth_id,
        }

        await rw.add_flow([("write", {**request_token_in, **{"tan": 4}})])

        # Create a task that requests a token (without overriding the default timeout).
        task = asyncio.create_task(
            hc.async_request_token(comment=const.DEFAULT_ORIGIN, id=auth_id)
        )

        # Wait 2 times the default timeout (task should NOT have timed out)
        await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)
        self.assertFalse(task.done())
        await self.advance(const.DEFAULT_REQUEST_TOKEN_TIMEOUT_SECS)
        self.assertTrue(task.done())
        self.assertFalse(await task)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_send_and_receive(self) -> None:
        """Test a send and receive wrapper."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        clear_in = {"command": "clear", "priority": 50, "tan": 2}
        clear_out = {"command": "clear", "success": True, "tan": 2}

        # == Successful request & response.
        await rw.add_flow([("write", clear_in), ("read", clear_out)])
        self.assertEqual(clear_out, await hc.async_clear(priority=50))

        # == Successful request & failed response.
        clear_out["success"] = False
        clear_out["tan"] = clear_in["tan"] = 3

        await rw.add_flow([("write", clear_in), ("read", clear_out)])
        self.assertEqual(clear_out, await hc.async_clear(priority=50))

        # == Mismatch tan / timeout
        # Test when the result doesn't include a matching tan (should time
        # out). See related bug to include tan wherever possible:
        #
        # https://github.com/hyperion-project/hyperion.ng/issues/1001
        clear_error = {
            "command": "clear",
            "error": "Errors during specific message validation, "
            "please consult the Hyperion Log",
            "success": False,
            "tan": 0,
        }
        clear_in["tan"] = 4

        await rw.add_flow([("write", clear_in), ("read", clear_error)])

        task = asyncio.create_task(hc.async_clear(priority=50))
        await rw.block_until_flow_empty()
        await self.advance(const.DEFAULT_TIMEOUT_SECS * 2)
        self.assertEqual(None, await task)

        # == Exception thrown in send.
        await rw.add_flow([("write", ConnectionError())])
        result = await hc.async_clear(**clear_in)
        self.assertEqual(result, None)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_using_custom_tan(self) -> None:
        """Test a send and receive wrapper."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        clear_in = {"command": "clear", "priority": 50, "tan": 100}
        clear_out = {"command": "clear", "success": True, "tan": 100}

        # Test a successful call with a custom tan.
        await rw.add_flow([("write", clear_in), ("read", clear_out)])
        self.assertEqual(clear_out, await hc.async_clear(priority=50, tan=100))

        # Test a call with a duplicate tan (will raise an exception).
        await rw.add_flow([("write", clear_in), ("read", clear_out)])

        with self.assertRaises(client.HyperionClientTanNotAvailable):
            await asyncio.gather(
                hc.async_clear(priority=50, tan=100),
                hc.async_clear(priority=50, tan=100),
            )
        await rw.assert_flow_finished()

        # Test a custom tan and an automated tan, should succeed with the automated
        # tan choosing the next number.
        clear_in_1 = {"command": "clear", "priority": 50, "tan": 1}
        clear_in_2 = {"command": "clear", "priority": 50, "tan": 2}
        clear_out_1 = {"command": "clear", "success": True, "tan": 1}
        clear_out_2 = {"command": "clear", "success": True, "tan": 2}

        await rw.add_flow(
            [
                ("write", clear_in_1),
                ("write", clear_in_2),
                ("read", clear_out_1),
                ("read", clear_out_2),
            ]
        )

        result_a, result_b = await asyncio.gather(
            hc.async_clear(priority=50, tan=1),
            hc.async_clear(priority=50),
        )
        self.assertEqual(result_a, clear_out_1)
        self.assertEqual(result_b, clear_out_2)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_async_send_calls_have_await_call(self) -> None:
        """Verify async_send_* methods have an async_* pair."""
        for name, value in inspect.getmembers(client.HyperionClient):
            if name.startswith("async_send_") and callable(value):
                new_name = "async_" + name[len("async_send_") :]
                wrapper = getattr(client.HyperionClient, new_name, None)
                self.assertIsNotNone(wrapper)

                # wrapper.func -> Returns a partial for AwaitResponseWrapper.__call__()
                #     .__self__ -> AwaitResponseWrapper
                #         ._coro -> The wrapped coroutine within AwaitResponseWrapper.
                # pylint: disable=protected-access
                self.assertEqual(wrapper.func.__self__._coro, value)

    async def test_double_connect(self) -> None:
        """Test the behavior of a double connect call."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            self.assertTrue(await hc.async_client_connect())
            self.assertTrue(hc.is_connected)

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_double_disconnect(self) -> None:
        """Test the behavior of a double disconnect call."""
        (rw, hc) = await self._create_and_test_basic_connected_client()
        await self._disconnect_and_assert_finished(rw, hc)
        self.assertTrue(await hc.async_client_disconnect())

    async def test_generate_random_auth_id(self) -> None:
        """Test arandomly generated auth id."""
        random_id = client.generate_random_auth_id()
        self.assertEqual(5, len(random_id))
        for c in random_id:
            self.assertTrue(c in string.ascii_letters + string.digits)

    async def test_sysinfo(self) -> None:
        """Test the sysinfo command."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        sysinfo_in = {"command": "sysinfo", "tan": 2}
        sysinfo_out: Dict[str, Any] = {
            **TEST_SYSINFO_RESPONSE,
            "tan": 2,
        }

        await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
        sysinfo = await hc.async_sysinfo()
        self.assertEqual(sysinfo_out, sysinfo)
        await self._disconnect_and_assert_finished(rw, hc)

    async def test_sysinfo_id(self) -> None:
        """Verify fetching the sysinfo id."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        sysinfo_in = {"command": "sysinfo", "tan": 2}
        sysinfo_out: Dict[str, Any] = {
            **TEST_SYSINFO_RESPONSE,
            "tan": 2,
        }

        await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
        self.assertEqual(TEST_SYSINFO_ID, await hc.async_sysinfo_id())

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_sysinfo_version(self) -> None:
        """Verify fetching the sysinfo version."""
        (rw, hc) = await self._create_and_test_basic_connected_client()

        sysinfo_in = {"command": "sysinfo", "tan": 2}
        sysinfo_out: Dict[str, Any] = {
            **TEST_SYSINFO_RESPONSE,
            "tan": 2,
        }

        await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
        self.assertEqual(TEST_SYSINFO_VERSION, await hc.async_sysinfo_version())

        await self._disconnect_and_assert_finished(rw, hc)

    async def test_context_manager(self) -> None:
        """Test the context manager functionality."""
        rw = MockStreamReaderWriter(
            [
                ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
                ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 1}}),
            ]
        )

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            async with client.HyperionClient(TEST_HOST, TEST_PORT) as hc:
                assert hc
                self.assertTrue(hc.is_connected)
                await rw.assert_flow_finished()
                await rw.add_flow([("close", None)])
            await self._block_until_done(rw)
            await rw.assert_flow_finished()
            self.assertFalse(hc.is_connected)


class ResponseTestCase(unittest.TestCase):
    """Test case for the Hyperion Client Response object."""

    def test_response(self) -> None:
        """Test a variety of responses."""

        # Intentionally pass the wrong type.
        self.assertFalse(client.ResponseOK(["not", "a", "dict"]))  # type: ignore
        self.assertFalse(client.ResponseOK({"data": 1}))
        self.assertFalse(client.ResponseOK({const.KEY_SUCCESS: False}))
        self.assertTrue(client.ResponseOK({const.KEY_SUCCESS: True}))
