"""Test for the Hyperion Client."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import logging
import os
import string
from typing import Any, AsyncGenerator, cast
from unittest.mock import Mock, call, patch

import pytest

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


async def _exhaust_callbacks(event_loop: asyncio.AbstractEventLoop) -> None:
    """Run the loop until all ready callbacks are executed."""
    loop = cast(asyncio.BaseEventLoop, event_loop)
    while loop._ready:  # type: ignore[attr-defined]
        await asyncio.sleep(0)


class MockStreamReaderWriter:
    """A simple mocl StreamReader and StreamWriter."""

    def __init__(self, flow: list[tuple[str, Any]] | None = None) -> None:
        """Initializse the mock."""
        self._flow = flow or []
        self._read_cv = asyncio.Condition()
        self._write_cv = asyncio.Condition()
        self._flow_cv = asyncio.Condition()
        self._data_to_drain: bytes | None = None

    async def add_flow(self, flow: list[tuple[str, Any]]) -> None:
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

    async def _pop_flow(self) -> tuple[str, Any]:
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
        _LOGGER.debug("MockStreamReaderWriter: readline()")
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

            _LOGGER.debug(
                "MockStreamReaderWriter: readline() -> %s[...]" % str(data)[:100]
            )
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


# This is inspired by asynctest.ClockedTestCase (which this code originally used). This
# functionality is not natively supported in pytest-asyncio. The below is inspired by a
# PR for pytest-asyncio that implements similar clock "advancing":
#
# https://github.com/pytest-dev/pytest-asyncio/pull/113


class EventLoopClockAdvancer:
    """Allow advancing of loop time."""

    __slots__ = ("offset", "loop", "_base_time")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize."""
        self.offset = 0.0
        self._base_time = loop.time
        self.loop = loop

        # incorporate offset timing into the event loop
        self.loop.time = self.time  # type: ignore[assignment]

    def time(self) -> float:
        """Return loop time adjusted by offset."""
        return self._base_time() + self.offset

    async def __call__(self, seconds: float) -> None:
        """Advance time by a given offset in seconds."""
        # Exhaust all callbacks.
        await _exhaust_callbacks(self.loop)

        if seconds > 0:
            # advance the clock by the given offset
            self.offset += seconds

            # Once the clock is adjusted, new tasks may have just been
            # scheduled for running in the next pass through the event loop
            await _exhaust_callbacks(self.loop)


@pytest.fixture
def advance_time(event_loop: asyncio.AbstractEventLoop) -> EventLoopClockAdvancer:
    """Advance loop time."""
    return EventLoopClockAdvancer(event_loop)


async def _block_until_done(rw: MockStreamReaderWriter) -> None:
    await rw.block_until_flow_empty()
    await _exhaust_callbacks(asyncio.get_event_loop())


async def _disconnect_and_assert_finished(
    rw: MockStreamReaderWriter, hc: client.HyperionClient
) -> None:
    """Disconnect and assert clean disconnection."""
    await rw.add_flow([("close", None)])
    assert await hc.async_client_disconnect()
    await _block_until_done(rw)
    assert not hc.is_connected
    await rw.assert_flow_finished()


async def _create_client_and_connect(
    rw: MockStreamReaderWriter,
    *args: Any,
    **kwargs: Any,
) -> client.HyperionClient:
    """Create a HyperionClient and connect it."""
    with patch("asyncio.open_connection", return_value=(rw, rw)):
        hc = client.HyperionClient(
            TEST_HOST,
            TEST_PORT,
            *args,
            **kwargs,
        )
        assert await hc.async_client_connect()
        assert hc.is_connected
    return hc


@pytest.fixture
async def rw(
    event_loop: asyncio.AbstractEventLoop,
) -> AsyncGenerator[MockStreamReaderWriter, None]:
    """Create a basic connected client object."""
    yield MockStreamReaderWriter(
        [
            ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
            ("read", {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 1}}),
        ]
    )


@dataclass
class HyperionFixture:
    """Data from a HyperionFixture."""

    rw: MockStreamReaderWriter
    hc: client.HyperionClient


@pytest.fixture
async def hyperion_fixture(
    event_loop: asyncio.AbstractEventLoop,
    rw: MockStreamReaderWriter,
) -> AsyncGenerator[HyperionFixture, None]:
    """Create a basic connected client object."""
    hc = await _create_client_and_connect(rw)
    await rw.assert_flow_finished()
    yield HyperionFixture(rw, hc)
    await _disconnect_and_assert_finished(rw, hc)


@pytest.mark.asyncio
async def test_async_client_connect_success(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test async connection to server."""


@pytest.mark.asyncio
async def test_async_client_connect_failure(
    event_loop: asyncio.AbstractEventLoop,
) -> None:
    """Test failed connection to server."""

    # == Try to connect when the token fails.
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

    with patch("asyncio.open_connection", return_value=(rw, rw)):
        hc = client.HyperionClient(TEST_HOST, TEST_PORT, token=TEST_TOKEN)
        assert not await hc.async_client_connect()
        assert not hc.is_connected
        await rw.assert_flow_finished()

    # == Try to connect when the instance selection fails.
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

    with patch("asyncio.open_connection", return_value=(rw, rw)):
        hc = client.HyperionClient(TEST_HOST, TEST_PORT, instance=TEST_INSTANCE)
        assert not await hc.async_client_connect()
        assert not hc.is_connected
        await rw.assert_flow_finished()

    # == Try to connect when the serverinfo (state load) call fails.
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

    with patch("asyncio.open_connection", return_value=(rw, rw)):
        hc = client.HyperionClient(TEST_HOST, TEST_PORT)
        assert not await hc.async_client_connect()
        assert not hc.is_connected
        await rw.assert_flow_finished()


@pytest.mark.asyncio
async def test_async_client_connect_specified_instance(
    event_loop: asyncio.AbstractEventLoop,
) -> None:
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

    hc = await _create_client_and_connect(rw, instance=TEST_INSTANCE)
    assert hc.instance == TEST_INSTANCE
    await _disconnect_and_assert_finished(rw, hc)


@pytest.mark.asyncio
async def test_async_client_connect_raw(event_loop: asyncio.AbstractEventLoop) -> None:
    """Test a raw connection."""
    rw = MockStreamReaderWriter()
    hc = await _create_client_and_connect(
        rw,
        instance=TEST_INSTANCE,
        token=TEST_TOKEN,
        raw_connection=True,
    )

    # It's a raw connection, it will not be logged in, nor instance selected.
    assert hc.is_connected
    assert not hc.is_logged_in
    assert hc.instance == const.DEFAULT_INSTANCE
    assert not hc.has_loaded_state

    # Manually log in.
    auth_login_in = {
        "command": "authorize",
        "subcommand": "login",
        "token": TEST_TOKEN,
        "tan": 1,
    }
    auth_login_out = {"command": "authorize-login", "success": True, "tan": 1}

    await rw.add_flow([("write", auth_login_in), ("read", auth_login_out)])
    assert await hc.async_client_login()

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

    assert await hc.async_client_switch_instance()
    assert await hc.async_get_serverinfo()
    await _disconnect_and_assert_finished(rw, hc)


@pytest.mark.asyncio
async def test_instance_switch_causes_empty_state(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test that an instance will have no state after an instance switch."""

    hc = await _create_client_and_connect(rw)
    assert hc.instance == const.DEFAULT_INSTANCE

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

    assert await hc.async_send_switch_instance(instance=instance)
    await rw.block_until_flow_empty()

    assert hc.is_connected
    assert hc.instance == instance
    assert hc.target_instance == instance
    assert not hc.has_loaded_state

    await _disconnect_and_assert_finished(rw, hc)

    # Ensure there is no live instance, but that the target instance is the
    # one that was switched to.
    assert hc.target_instance == instance
    assert hc.instance is None


@pytest.mark.asyncio
async def test_receive_wrong_data_type(
    event_loop: asyncio.AbstractEventLoop, advance_time: EventLoopClockAdvancer
) -> None:
    """Test that receiving the wrong data-type is handled."""
    rw = MockStreamReaderWriter(
        [
            ("write", {**SERVERINFO_REQUEST, **{"tan": 1}}),
        ]
    )

    hc = await _create_client_and_connect(rw, raw_connection=True)

    task = asyncio.create_task(hc.async_get_serverinfo())
    await rw.block_until_flow_empty()
    await rw.add_flow(
        [
            ("read", ["this", "is", "not", "a", "dict"]),
        ]
    )
    await advance_time(const.DEFAULT_TIMEOUT_SECS)
    assert not await task
    await _disconnect_and_assert_finished(rw, hc)


@pytest.mark.asyncio
async def test_is_on(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test the client reports correctly on whether components are on."""
    hc = hyperion_fixture.hc

    with open(_get_test_filepath(FILE_SERVERINFO_RESPONSE)) as fh:
        serverinfo_command_response = fh.readline()
    serverinfo = json.loads(serverinfo_command_response)

    # Verify server info is as expected.
    assert hc.serverinfo == serverinfo[const.KEY_INFO]

    # Verify the individual components.
    assert hc.is_on(components=[const.KEY_COMPONENTID_ALL])
    assert hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING])
    assert hc.is_on(components=[const.KEY_COMPONENTID_BLACKBORDER])
    assert not hc.is_on(components=[const.KEY_COMPONENTID_FORWARDER])
    assert not hc.is_on(components=[const.KEY_COMPONENTID_BOBLIGHTSERVER])
    assert not hc.is_on(components=[const.KEY_COMPONENTID_GRABBER])
    assert hc.is_on(components=[const.KEY_COMPONENTID_V4L])
    assert hc.is_on(components=[const.KEY_COMPONENTID_LEDDEVICE])

    # Verify combinations.
    assert hc.is_on(
        components=[
            const.KEY_COMPONENTID_ALL,
            const.KEY_COMPONENTID_SMOOTHING,
            const.KEY_COMPONENTID_BLACKBORDER,
        ]
    )

    assert not hc.is_on(
        components=[
            const.KEY_COMPONENTID_ALL,
            const.KEY_COMPONENTID_GRABBER,
            const.KEY_COMPONENTID_BLACKBORDER,
        ]
    )

    # Verify default.
    assert hc.is_on()


@pytest.mark.asyncio
async def test_update_component(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating components."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    # === Verify flipping a component.
    components_update = {
        "command": "components-update",
        "data": {"enabled": False, "name": "SMOOTHING"},
    }

    assert hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING])

    await rw.add_flow([("read", components_update)])
    await _block_until_done(rw)

    assert not hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING])

    # === Verify a component change where the component name is not existing.
    component_name = "NOT_EXISTING"
    components_update = {
        "command": "components-update",
        "data": {"enabled": True, "name": component_name},
    }

    assert not hc.is_on(components=[component_name])

    await rw.add_flow([("read", components_update)])
    await _block_until_done(rw)
    assert hc.is_on(components=[component_name])


@pytest.mark.asyncio
async def test_update_adjustment(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating adjustments."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    adjustment_update = {
        "command": "adjustment-update",
        "data": [{"brightness": 25}],
    }
    assert hc.adjustment
    assert hc.adjustment[0]["brightness"] == 83

    await rw.add_flow([("read", adjustment_update)])
    await _block_until_done(rw)

    assert hc.adjustment[0]["brightness"] == 25


@pytest.mark.asyncio
async def test_update_effect_list(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating effect list."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

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
    await _block_until_done(rw)

    assert hc.effects
    assert len(hc.effects) == 1
    assert hc.effects[0] == effect


@pytest.mark.asyncio
async def test_update_priorities(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating priorities."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

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
    priorities_update: dict[str, Any] = {
        "command": "priorities-update",
        "data": {"priorities": priorities, "priorities_autoselect": False},
    }

    assert hc.priorities
    assert len(hc.priorities) == 2
    assert hc.priorities_autoselect
    assert hc.visible_priority
    assert hc.visible_priority["priority"] == 240

    await rw.add_flow([("read", priorities_update)])
    await _block_until_done(rw)

    assert hc.priorities == priorities
    assert hc.visible_priority == priorities[0]
    assert not bool(hc.priorities_autoselect)

    priorities_update = {
        "command": "priorities-update",
        "data": {"priorities": [], "priorities_autoselect": True},
    }

    await rw.add_flow([("read", priorities_update)])
    await _block_until_done(rw)

    assert hc.priorities_autoselect is not None
    assert hc.visible_priority is None


@pytest.mark.asyncio
async def test_update_instances(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test updating instances."""

    hc = await _create_client_and_connect(rw)
    assert hc.instances
    assert len(hc.instances) == 2
    assert hc.instance == 0
    assert hc.target_instance == 0

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
    await _block_until_done(rw)
    assert hc.instances == instances

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

    # Because the target instance is no longer running, the client should disconnect
    # automatically.
    await _block_until_done(rw)
    assert not hc.is_connected
    await rw.assert_flow_finished()
    assert hc.target_instance == 0
    assert hc.instance is None


@pytest.mark.asyncio
async def test_update_led_mapping_type(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating LED mapping type."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    led_mapping_type = "unicolor_mean"
    led_mapping_type_update = {
        "command": "imageToLedMapping-update",
        "data": {"imageToLedMappingType": led_mapping_type},
    }

    assert hc.led_mapping_type != led_mapping_type
    await rw.add_flow([("read", led_mapping_type_update)])
    await _block_until_done(rw)
    assert hc.led_mapping_type == led_mapping_type


@pytest.mark.asyncio
async def test_update_sessions(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating sessions."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

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

    assert hc.sessions == []
    await rw.add_flow([("read", sessions_update)])
    await _block_until_done(rw)
    assert hc.sessions == sessions


@pytest.mark.asyncio
async def test_videomode(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating videomode."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    videomode = "3DSBS"

    videomode_update = {
        "command": "videomode-update",
        "data": {"videomode": videomode},
    }

    assert hc.videomode == "2D"
    await rw.add_flow([("read", videomode_update)])
    await _block_until_done(rw)
    assert hc.videomode == videomode


@pytest.mark.asyncio
async def test_update_leds(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test updating LEDs."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    leds = [{"hmin": 0.0, "hmax": 1.0, "vmin": 0.0, "vmax": 1.0}]
    leds_update = {"command": "leds-update", "data": {"leds": leds}}

    assert hc.leds
    assert len(hc.leds) == 254
    await rw.add_flow([("read", leds_update)])
    await _block_until_done(rw)
    assert hc.leds == leds


@pytest.mark.asyncio
async def test_async_send_set_color(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test controlling color."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    color_in = {
        "color": [0, 0, 255],
        "command": "color",
        "origin": "My Fancy App",
        "priority": 50,
    }

    await rw.add_flow([("write", color_in)])
    assert await hc.async_send_set_color(**color_in)
    await _block_until_done(rw)

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
    assert await hc.async_send_set_color(**color_in)
    await _block_until_done(rw)


@pytest.mark.asyncio
async def test_async_send_set_effect(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test controlling effect."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    effect_in = {
        "command": "effect",
        "effect": {"name": "Warm mood blobs"},
        "priority": 50,
        "origin": "My Fancy App",
    }

    await rw.add_flow([("write", effect_in)])
    assert await hc.async_send_set_effect(**effect_in)

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
    assert await hc.async_send_set_effect(**effect_in)


@pytest.mark.asyncio
async def test_async_send_set_image(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test controlling image."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
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
    assert await hc.async_send_set_image(**image_in)

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
    assert await hc.async_send_set_image(**image_in)


@pytest.mark.asyncio
async def test_async_send_clear(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test clearing priorities."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    clear_in = {
        "command": "clear",
        "priority": 50,
    }

    await rw.add_flow([("write", clear_in)])
    assert await hc.async_send_clear(**clear_in)
    await rw.add_flow([("write", clear_in)])
    assert await hc.async_send_clear(priority=50)


@pytest.mark.asyncio
async def test_async_send_set_adjustment(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test setting adjustment."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    adjustment_in = {"command": "adjustment", "adjustment": {"gammaRed": 1.5}}

    await rw.add_flow([("write", adjustment_in)])
    assert await hc.async_send_set_adjustment(**adjustment_in)
    await rw.add_flow([("write", adjustment_in)])
    assert await hc.async_send_set_adjustment(adjustment={"gammaRed": 1.5})


@pytest.mark.asyncio
async def test_async_send_set_led_mapping_type(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Test setting adjustment."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    led_mapping_type_in = {
        "command": "processing",
        "mappingType": "multicolor_mean",
    }

    await rw.add_flow([("write", led_mapping_type_in)])
    assert await hc.async_send_set_led_mapping_type(**led_mapping_type_in)
    await rw.add_flow([("write", led_mapping_type_in)])
    assert await hc.async_send_set_led_mapping_type(mappingType="multicolor_mean")


@pytest.mark.asyncio
async def test_async_send_set_videomode(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test setting videomode."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    videomode_in = {"command": "videomode", "videoMode": "3DTAB"}

    await rw.add_flow([("write", videomode_in)])
    assert await hc.async_send_set_videomode(**videomode_in)
    await rw.add_flow([("write", videomode_in)])
    assert await hc.async_send_set_videomode(videoMode="3DTAB")


@pytest.mark.asyncio
async def test_async_send_set_component(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test setting component."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    componentstate = {
        "component": "LEDDEVICE",
        "state": False,
    }
    component_in = {
        "command": "componentstate",
        "componentstate": componentstate,
    }

    await rw.add_flow([("write", component_in)])
    assert await hc.async_send_set_component(**component_in)
    await rw.add_flow([("write", component_in)])
    assert await hc.async_send_set_component(componentstate=componentstate)


@pytest.mark.asyncio
async def test_async_send_set_sourceselect(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test setting sourceselect."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    sourceselect_in = {"command": "sourceselect", "priority": 50}

    await rw.add_flow([("write", sourceselect_in)])
    assert await hc.async_send_set_sourceselect(**sourceselect_in)
    await rw.add_flow([("write", sourceselect_in)])
    assert await hc.async_send_set_sourceselect(priority=50)


@pytest.mark.asyncio
async def test_start_async_send_stop_switch_instance(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Test starting, stopping and switching instances."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    start_in = {"command": "instance", "subcommand": "startInstance", "instance": 1}

    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_start_instance(**start_in)
    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_start_instance(instance=1)

    stop_in = {"command": "instance", "subcommand": "stopInstance", "instance": 1}

    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_stop_instance(**stop_in)
    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_stop_instance(instance=1)

    switch_in = {"command": "instance", "subcommand": "switchTo", "instance": 1}

    await rw.add_flow([("write", switch_in)])
    assert await hc.async_send_switch_instance(**switch_in)
    await rw.add_flow([("write", switch_in)])
    assert await hc.async_send_switch_instance(instance=1)


@pytest.mark.asyncio
async def test_start_async_send_stop_image_stream(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Test starting and stopping an image stream."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    start_in = {"command": "ledcolors", "subcommand": "imagestream-start"}

    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_image_stream_start(**start_in)
    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_image_stream_start()

    stop_in = {"command": "ledcolors", "subcommand": "imagestream-stop"}

    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_image_stream_stop(**stop_in)
    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_image_stream_stop()


@pytest.mark.asyncio
async def test_async_send_start_stop_led_stream(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Test starting and stopping an led stream."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    start_in = {"command": "ledcolors", "subcommand": "ledstream-start"}

    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_led_stream_start(**start_in)
    await rw.add_flow([("write", start_in)])
    assert await hc.async_send_led_stream_start()

    stop_in = {"command": "ledcolors", "subcommand": "ledstream-stop"}

    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_led_stream_stop(**stop_in)
    await rw.add_flow([("write", stop_in)])
    assert await hc.async_send_led_stream_stop()


@pytest.mark.asyncio
# pylint: disable=too-many-statements
async def test_callbacks(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test updating components."""
    cb = Mock()

    hc = await _create_client_and_connect(
        rw,
        default_callback=cb.default_callback,
        callbacks={
            "components-update": cb.component_callback,
            "serverinfo": cb.serverinfo_callback,
            "client-update": cb.client_callback,
        },
    )

    assert cb.client_callback.call_args_list == [
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
    ]

    assert cb.serverinfo_callback.call_args[0][0] == _read_file(
        FILE_SERVERINFO_RESPONSE
    )
    cb.reset_mock()

    # === Flip a component.
    components_update = {
        "command": "components-update",
        "data": {"enabled": False, "name": "SMOOTHING"},
    }

    # Make sure the callback was called.
    await rw.add_flow([("read", components_update)])
    await _block_until_done(rw)

    cb.default_callback.assert_not_called()
    cb.component_callback.assert_called_once_with(components_update)
    cb.reset_mock()

    # Call with a new update that does not have a registered callback.
    random_update_value = "random-update"
    random_update = {
        "command": random_update_value,
    }
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)

    cb.default_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    # Now set a callback for that update.
    hc.set_callbacks({random_update_value: cb.first_callback})
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.first_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    # Now add a second callback for that update.
    hc.add_callbacks({random_update_value: cb.second_callback})
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.first_callback.assert_called_once_with(random_update)
    cb.second_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    # Now add multiple callbacks.
    hc.add_callbacks({random_update_value: [cb.third_callback, cb.fourth_callback]})
    hc.add_callbacks({})
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.first_callback.assert_called_once_with(random_update)
    cb.second_callback.assert_called_once_with(random_update)
    cb.third_callback.assert_called_once_with(random_update)
    cb.fourth_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    # Set multiple callbacks (effectively removing  a few).
    hc.set_callbacks({random_update_value: [cb.third_callback, cb.fourth_callback]})
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
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
    await _block_until_done(rw)
    cb.first_callback.assert_called_once_with(random_update)
    cb.second_callback.assert_called_once_with(random_update)
    cb.third_callback.assert_not_called()
    cb.fourth_callback.assert_not_called()
    cb.null_callback.assert_not_called()
    cb.reset_mock()

    # Remove all callbacks.
    hc.set_callbacks(None)
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.first_callback.assert_not_called()
    cb.second_callback.assert_not_called()
    cb.reset_mock()

    # Add another default callback.
    hc.add_default_callback(cb.second_default_callback)
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.default_callback.assert_called_once_with(random_update)
    cb.second_default_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    # Remove a default callback.
    hc.remove_default_callback(cb.default_callback)
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.default_callback.assert_not_called()
    cb.second_default_callback.assert_called_once_with(random_update)
    cb.reset_mock()

    awaitable_json = None

    async def awaitable_callback(arg: dict[str, Any]) -> None:
        nonlocal awaitable_json
        awaitable_json = arg

    # Set an async default callback.
    hc.set_default_callback(awaitable_callback)
    await rw.add_flow([("read", random_update)])
    await _block_until_done(rw)
    cb.default_callback.assert_not_called()
    cb.second_default_callback.assert_not_called()
    assert awaitable_json == random_update

    # Verify disconnection callback.
    hc.set_callbacks({"client-update": cb.client_callback})
    await _disconnect_and_assert_finished(rw, hc)

    cb.client_callback.assert_called_once_with(
        {
            "command": "client-update",
            "connected": False,
            "instance": None,
            "loaded-state": False,
            "logged-in": False,
        },
    )


@pytest.mark.asyncio
async def test_is_auth_required(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test determining if authorization is required."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    auth_request = {"command": "authorize", "subcommand": "tokenRequired", "tan": 2}

    auth_response = {
        "command": "authorize-tokenRequired",
        "info": {"required": True},
        "success": True,
        "tan": 2,
    }

    await rw.add_flow([("write", auth_request), ("read", auth_response)])
    received = await hc.async_is_auth_required()
    await _block_until_done(rw)
    assert received == auth_response


@pytest.mark.asyncio
async def test_async_send_login(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test setting videomode."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    token = "sekrit"
    auth_login_in = {
        "command": "authorize",
        "subcommand": "login",
        "token": token,
    }

    await rw.add_flow([("write", auth_login_in)])
    assert await hc.async_send_login(**auth_login_in)
    await rw.add_flow([("write", auth_login_in)])
    assert await hc.async_send_login(token=token)


@pytest.mark.asyncio
async def test_disconnecting_leaves_no_tasks(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Verify stopping the background task."""
    before_tasks = asyncio.all_tasks()

    hc = await _create_client_and_connect(rw)
    await _disconnect_and_assert_finished(rw, hc)

    assert before_tasks == asyncio.all_tasks()


@pytest.mark.asyncio
async def test_async_send_logout(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test setting videomode."""
    before_tasks = asyncio.all_tasks()
    hc = await _create_client_and_connect(rw)

    auth_logout_in = {
        "command": "authorize",
        "subcommand": "logout",
    }

    await rw.add_flow([("write", auth_logout_in)])
    assert await hc.async_send_logout(**auth_logout_in)
    await rw.add_flow([("write", auth_logout_in)])
    assert await hc.async_send_logout()

    # A logout success response should cause the client to disconnect.
    auth_logout_out = {
        "command": "authorize-logout",
        "success": True,
    }

    await rw.add_flow([("read", auth_logout_out), ("close", None)])

    await _block_until_done(rw)
    assert not hc.is_connected
    await rw.assert_flow_finished()

    # Verify there are no tasks left running (logout is interesting
    # in that the disconnection is from the receive task, so cancellation
    # of the receive task could cut the disconnection process off).
    assert before_tasks == asyncio.all_tasks()


@pytest.mark.asyncio
async def test_async_send_request_token(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test requesting an auth token."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    # Test requesting a token.
    request_token_in: dict[str, Any] = {
        "command": "authorize",
        "subcommand": "requestToken",
        "comment": "Test",
        "id": "T3c92",
    }

    await rw.add_flow([("write", request_token_in)])
    assert await hc.async_send_request_token(**request_token_in)

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

    assert await hc.async_send_request_token(**small_request_token_in)

    # Abort a request for a token.
    request_token_in["accept"] = False
    await rw.add_flow([("write", request_token_in)])
    assert await hc.async_send_request_token_abort(**request_token_in)


@pytest.mark.asyncio
async def test_async_send_serverinfo(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test requesting serverinfo."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    await rw.add_flow([("write", SERVERINFO_REQUEST)])
    assert await hc.async_send_get_serverinfo(**SERVERINFO_REQUEST)


def test_threaded_client() -> None:
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
        assert not hc.client_connect()
        assert not hc.is_connected

    hc.stop()
    hc.join()


def test_threaded_client_has_correct_methods() -> None:
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
            assert name[len("async_") :] in contents

    for name, _ in inspect.getmembers(
        client.ThreadedHyperionClient, lambda o: isinstance(o, property)
    ):
        assert name in contents


@pytest.mark.asyncio
async def test_client_write_and_close_handles_network_issues(
    event_loop: asyncio.AbstractEventLoop,
    rw: MockStreamReaderWriter,
) -> None:
    """Verify sending data does not throw exceptions."""
    hc = await _create_client_and_connect(rw)

    # Verify none of these write operations result in an exception
    # propagating to the test.
    await rw.add_flow([("write", ConnectionError("Write exception"))])
    assert not await hc.async_send_image_stream_start()

    await rw.add_flow([("close", ConnectionError("Close exception"))])
    assert not await hc.async_client_disconnect()

    await rw.assert_flow_finished()


@pytest.mark.asyncio
async def test_client_handles_network_issues_bad_read(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Verify a bad read causes a reconnection."""

    rw = hyperion_fixture.rw

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
        await _block_until_done(rw)


@pytest.mark.asyncio
async def test_client_handles_network_issues_unexpected_close(
    event_loop: asyncio.AbstractEventLoop,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Verify an unexpected close causes a reconnection."""

    # == Verify an empty read causes a disconnect and reconnect.
    (rw, _) = hyperion_fixture.rw, hyperion_fixture.hc

    # == Read returns empty, connection closed, but instantly re-established.
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
        await _block_until_done(rw)


@pytest.mark.asyncio
async def test_client_handles_network_issues_bad_read_cannot_reconnect_ads(
    event_loop: asyncio.AbstractEventLoop,
    advance_time: EventLoopClockAdvancer,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Verify behavior after a bad read when the connection cannot be re-established."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    # == Read throws an exception, connection closed, cannot be re-established.
    with patch("asyncio.open_connection", side_effect=ConnectionError):
        await rw.add_flow([("read", ConnectionError("Read error")), ("close", None)])
        await rw.block_until_flow_empty()

        # Wait f"{const.DEFAULT_CONNECTION_RETRY_DELAY_SECS}" seconds and then the
        # connection should be re-established.

        # Check at half that timeout that we're still not connected.
        assert not hc.is_connected
        await advance_time(const.DEFAULT_CONNECTION_RETRY_DELAY_SECS / 2)
        assert not hc.is_connected

        # Stage 4: Fast-forward the remaining half of the timeout (+1 to ensure
        # we're definitely on the far side of the timeout), and it should
        # automatically reload.

        with patch("asyncio.open_connection", return_value=(rw, rw)):
            await rw.add_flow(
                [
                    ("write", {**SERVERINFO_REQUEST, **{"tan": 2}}),
                    (
                        "read",
                        {**_read_file(FILE_SERVERINFO_RESPONSE), **{"tan": 2}},
                    ),
                ]
            )
            await advance_time((const.DEFAULT_CONNECTION_RETRY_DELAY_SECS / 2) + 1)
            await _block_until_done(rw)


@pytest.mark.asyncio
async def test_client_connect_handles_network_issues_cannot_reconnect_connection_error(
    event_loop: asyncio.AbstractEventLoop,
    rw: MockStreamReaderWriter,
) -> None:
    """Verify connecting does throw exceptions and behaves correctly."""
    hc = await _create_client_and_connect(rw)

    with patch(
        "asyncio.open_connection",
        side_effect=ConnectionError("Connection exception"),
    ):
        await rw.add_flow([("read", ""), ("close", None)])
        await rw.block_until_flow_empty()
        assert not hc.is_connected

        # Disconnect to avoid it attempting to reconnect.
        assert await hc.async_client_disconnect()
        await rw.assert_flow_finished()


@pytest.mark.asyncio
async def test_client_connection_timeout(event_loop: asyncio.AbstractEventLoop) -> None:
    """Verify connection and read timeouts behave correctly."""

    # == Verify timeout is dealt with correctly during connection.
    with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError):
        hc = client.HyperionClient(TEST_HOST, TEST_PORT)
        assert not await hc.async_client_connect()


@pytest.mark.asyncio
async def test_client_timeout(
    event_loop: asyncio.AbstractEventLoop,
    advance_time: EventLoopClockAdvancer,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Verify connection and read timeouts behave correctly."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    # == Verify timeout is dealt with during read.
    await rw.add_flow([("write", {**SERVERINFO_REQUEST, **{"tan": 2}})])

    # Create a new task to get the serverinfo ...
    task = asyncio.create_task(hc.async_get_serverinfo())
    # ... wait until the flow is empty (i.e. the request is written to the
    # server).
    await rw.block_until_flow_empty()

    # Advance the clock so it times out waiting.
    await advance_time(const.DEFAULT_TIMEOUT_SECS * 2)

    # Ensuring the task fails.
    assert not await task

    # == Verify custom timeouts function in calls.
    await rw.add_flow([("write", {**SERVERINFO_REQUEST, **{"tan": 3}})])

    # Create a task thatL fetches serverinfo and will wait 3 times the default.
    task = asyncio.create_task(
        hc.async_get_serverinfo(timeout_secs=const.DEFAULT_TIMEOUT_SECS * 3)
    )

    # Wait 2 times the default (task should NOT have timed out)
    await advance_time(const.DEFAULT_TIMEOUT_SECS * 2)

    assert not task.done()

    # Wait a further two times (should have timed out)
    await advance_time(const.DEFAULT_TIMEOUT_SECS * 2)

    assert not await task
    assert task.done()

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
    await advance_time(const.DEFAULT_TIMEOUT_SECS * 2)
    assert not task.done()

    await advance_time(const.DEFAULT_REQUEST_TOKEN_TIMEOUT_SECS)
    assert not await task
    assert task.done()


@pytest.mark.asyncio
async def test_send_and_receive(
    event_loop: asyncio.AbstractEventLoop,
    advance_time: EventLoopClockAdvancer,
    hyperion_fixture: HyperionFixture,
) -> None:
    """Test a send and receive wrapper."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc
    clear_in = {"command": "clear", "priority": 50, "tan": 2}
    clear_out = {"command": "clear", "success": True, "tan": 2}

    # == Successful request & response.
    await rw.add_flow([("write", clear_in), ("read", clear_out)])
    assert await hc.async_clear(priority=50) == clear_out

    # == Successful request & failed response.
    clear_out["success"] = False
    clear_out["tan"] = clear_in["tan"] = 3

    await rw.add_flow([("write", clear_in), ("read", clear_out)])
    assert await hc.async_clear(priority=50) == clear_out

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
    await advance_time(const.DEFAULT_TIMEOUT_SECS * 2)
    assert await task is None

    # == Exception thrown in send.
    await rw.add_flow([("write", ConnectionError())])
    result = await hc.async_clear(**clear_in)
    assert result is None


@pytest.mark.asyncio
async def test_using_custom_tan(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test a send and receive wrapper."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    clear_in = {"command": "clear", "priority": 50, "tan": 100}
    clear_out = {"command": "clear", "success": True, "tan": 100}

    # Test a successful call with a custom tan.
    await rw.add_flow([("write", clear_in), ("read", clear_out)])
    assert await hc.async_clear(priority=50, tan=100) == clear_out

    # Test a call with a duplicate tan (will raise an exception).
    await rw.add_flow([("write", clear_in), ("read", clear_out)])

    with pytest.raises(client.HyperionClientTanNotAvailable):
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
    assert clear_out_1 == result_a
    assert clear_out_2 == result_b


async def test_async_send_calls_have_async_call(
    event_loop: asyncio.AbstractEventLoop,
) -> None:
    """Verify async_send_* methods have an async_* pair."""
    for name, value in inspect.getmembers(client.HyperionClient):
        if name.startswith("async_send_") and callable(value):
            new_name = "async_" + name[len("async_send_") :]
            wrapper = getattr(client.HyperionClient, new_name, None)
            assert wrapper is not None

            # wrapper.func -> Returns a partial for AwaitResponseWrapper.__call__()
            #     .__self__ -> AwaitResponseWrapper
            #         ._coro -> The wrapped coroutine within AwaitResponseWrapper.
            # pylint: disable=protected-access
            assert wrapper.func.__self__._coro == value


@pytest.mark.asyncio
async def test_double_connect(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test the behavior of a double connect call."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    with patch("asyncio.open_connection", return_value=(rw, rw)):
        assert await hc.async_client_connect()
        assert hc.is_connected


@pytest.mark.asyncio
async def test_double_disconnect(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test the behavior of a double disconnect call."""
    hc = await _create_client_and_connect(rw)
    await _disconnect_and_assert_finished(rw, hc)
    assert await hc.async_client_disconnect()


def test_generate_random_auth_id() -> None:
    """Test arandomly generated auth id."""
    random_id = client.generate_random_auth_id()
    assert len(random_id) == 5
    for c in random_id:
        assert c in (string.ascii_letters + string.digits)


@pytest.mark.asyncio
async def test_sysinfo(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Test the sysinfo command."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    sysinfo_in = {"command": "sysinfo", "tan": 2}
    sysinfo_out: dict[str, Any] = {
        **TEST_SYSINFO_RESPONSE,
        "tan": 2,
    }

    await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
    assert await hc.async_sysinfo() == sysinfo_out


@pytest.mark.asyncio
async def test_sysinfo_id(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Verify fetching the sysinfo id."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    sysinfo_in = {"command": "sysinfo", "tan": 2}
    sysinfo_out: dict[str, Any] = {
        **TEST_SYSINFO_RESPONSE,
        "tan": 2,
    }

    await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
    assert await hc.async_sysinfo_id() == TEST_SYSINFO_ID


@pytest.mark.asyncio
async def test_sysinfo_version(
    event_loop: asyncio.AbstractEventLoop, hyperion_fixture: HyperionFixture
) -> None:
    """Verify fetching the sysinfo version."""
    (rw, hc) = hyperion_fixture.rw, hyperion_fixture.hc

    sysinfo_in = {"command": "sysinfo", "tan": 2}
    sysinfo_out: dict[str, Any] = {
        **TEST_SYSINFO_RESPONSE,
        "tan": 2,
    }

    await rw.add_flow([("write", sysinfo_in), ("read", sysinfo_out)])
    assert await hc.async_sysinfo_version() == TEST_SYSINFO_VERSION


@pytest.mark.asyncio
async def test_context_manager(
    event_loop: asyncio.AbstractEventLoop, rw: MockStreamReaderWriter
) -> None:
    """Test the context manager functionality."""

    with patch("asyncio.open_connection", return_value=(rw, rw)):
        async with client.HyperionClient(TEST_HOST, TEST_PORT) as hc:
            assert hc
            assert hc.is_connected
            await rw.assert_flow_finished()
            await rw.add_flow([("close", None)])
        await _block_until_done(rw)
        await rw.assert_flow_finished()
        assert not hc.is_connected


async def test_response_ok() -> None:
    """Test case for the Hyperion Client ResponseOK class."""

    # Intentionally pass the wrong type.
    assert not client.ResponseOK(["not", "a", "dict"])  # type: ignore[arg-type]
    assert not client.ResponseOK({"data": 1})
    assert not client.ResponseOK({const.KEY_SUCCESS: False})
    assert client.ResponseOK({const.KEY_SUCCESS: True})
