#!/usr/bin/python
"""Test for the Hyperion Client."""

import asynctest
import asyncio
import json
import os
import unittest
from hyperion import client, const
import logging

logging.basicConfig()
logger = logging.getLogger(__name__)

# Change logging level here.
logger.setLevel(logging.DEBUG)

PATH_TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
TEST_HOST = "test"
TEST_PORT = 65000
TEST_TOKEN = "FAKE_TOKEN"
TEST_INSTANCE = 1

JSON_FILENAME_SERVERINFO_RESPONSE = "serverinfo_response_1.json"

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
}


class AsyncHyperionClientTestCase(asynctest.TestCase):
    """Test case for the Hyperion Client."""

    def _create_mock_reader(self, reads=None, filenames=None):
        reader = asynctest.mock.Mock(asyncio.StreamReader)
        if reads:
            self._add_expected_reads(reader, reads)
        if filenames:
            self._add_expected_reads_from_files(reader, filenames)
        return reader

    def _create_mock_writer(self):
        return asynctest.mock.Mock(asyncio.StreamWriter)

    def _add_expected_reads(self, reader, reads):
        side_effect = reader.readline.side_effect or []
        reader.readline.side_effect = [v for v in side_effect] + reads

    def _add_expected_reads_from_files(self, reader, filenames):
        reads = []
        for filename in filenames:
            with open(self._get_test_filepath(filename)) as fh:
                reads.extend(fh.readlines())
        self._add_expected_reads(reader, reads)

    def _get_test_filepath(self, filename):
        return os.path.join(PATH_TESTDATA, filename)

    def _verify_expected_writes(self, writer, writes=[], filenames=[]):
        for filename in filenames:
            with open(os.path.join(PATH_TESTDATA, filename)) as fh:
                writes.extend([line.encode("UTF-8") for line in fh.readlines()])

        call_index = 0
        while call_index < len(writer.method_calls) and int(call_index / 2) < len(
            writes
        ):
            self.assertEqual(
                writer.method_calls[call_index],
                unittest.mock.call.write(writes[int(call_index / 2)]),
            )
            self.assertEqual(
                writer.method_calls[call_index + 1], unittest.mock.call.drain
            )
            call_index += 2
        self.assertEqual(
            len(writer.method_calls) / 2,
            len(writes),
            msg="Incorrect number of write calls",
        )
        writer.reset_mock()

    def _verify_reader(self, reader):
        # The prepared responses should have been exhausted
        with self.assertRaises(StopIteration):
            reader.readline()

    def setUp(self):
        """Set up testcase."""
        pass

    def tearDown(self):
        """Tear down testcase."""
        pass

    async def _create_and_test_basic_connected_client(
        self, default_callback=None, callbacks=None
    ):
        """Create a basic connected client object."""
        reader = self._create_mock_reader(filenames=[JSON_FILENAME_SERVERINFO_RESPONSE])
        writer = self._create_mock_writer()

        with asynctest.mock.patch(
            "asyncio.open_connection", return_value=(reader, writer)
        ):
            hc = client.HyperionClient(
                TEST_HOST,
                TEST_PORT,
                loop=self.loop,
                default_callback=default_callback,
                callbacks=callbacks,
            )
            self.assertTrue(await hc.async_connect())

        self._verify_reader(reader)
        serverinfo_request_json = self._to_json_line(SERVERINFO_REQUEST)

        self._verify_expected_writes(writer, writes=[serverinfo_request_json])
        return (reader, writer, hc)

    async def test_async_connect(self):
        """Test async connection to server."""
        (_, _, hc) = await self._create_and_test_basic_connected_client()
        self.assertTrue(hc.is_connected)

    def _to_json_line(self, data):
        """Convert data to an encoded JSON string."""
        return (json.dumps(data, sort_keys=True) + "\n").encode("UTF-8")

    async def test_async_connect_authorized(self):
        """Test server connection with authorization."""
        authorize_request = {
            "command": "authorize",
            "subcommand": "login",
            "token": TEST_TOKEN,
        }
        authorize_response = {"command": "authorize-login", "success": True, "tan": 0}

        reader = self._create_mock_reader(
            reads=[self._to_json_line(authorize_response)],
            filenames=[JSON_FILENAME_SERVERINFO_RESPONSE],
        )
        writer = self._create_mock_writer()

        with asynctest.mock.patch(
            "asyncio.open_connection", return_value=(reader, writer)
        ):
            hc = client.HyperionClient(
                TEST_HOST, TEST_PORT, token=TEST_TOKEN, loop=self.loop
            )
            self.assertTrue(await hc.async_connect())

        self._verify_reader(reader)
        self._verify_expected_writes(
            writer,
            writes=[
                self._to_json_line(authorize_request),
                self._to_json_line(SERVERINFO_REQUEST),
            ],
        )

    async def test_async_connect_specified_instance(self):
        """Test server connection to specified instance."""
        instance_request = {
            "command": "instance",
            "instance": TEST_INSTANCE,
            "subcommand": "switchTo",
        }
        instance_response = {
            "command": "instance-switchTo",
            "success": True,
            "tan": 0,
            "info": {"instance": TEST_INSTANCE},
        }

        reader = self._create_mock_reader(
            reads=[self._to_json_line(instance_response)],
            filenames=[JSON_FILENAME_SERVERINFO_RESPONSE],
        )
        writer = self._create_mock_writer()

        with asynctest.mock.patch(
            "asyncio.open_connection", return_value=(reader, writer)
        ):
            hc = client.HyperionClient(
                TEST_HOST, TEST_PORT, instance=TEST_INSTANCE, loop=self.loop
            )
            self.assertTrue(await hc.async_connect())

        self.assertEqual(hc.instance, TEST_INSTANCE)

        self._verify_reader(reader)
        self._verify_expected_writes(
            writer,
            writes=[
                self._to_json_line(instance_request),
                self._to_json_line(SERVERINFO_REQUEST),
            ],
        )

    async def test_instance_switch_causes_refresh(self):
        """Test that an instance switch causes a full refresh."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        instance = 1
        switched = {
            "command": "instance-switchTo",
            "info": {"instance": instance},
            "success": True,
            "tan": 0,
        }
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)
        self._add_expected_reads(reader, reads=[self._to_json_line(switched)])
        self._add_expected_reads_from_files(
            reader, filenames=[JSON_FILENAME_SERVERINFO_RESPONSE]
        )

        await hc._async_manage_connection_once()
        self.assertEqual(hc.instance, instance)

        # Verify that post-disconnect the instance is preserved so next
        # connect() will re-join the same instance.
        await hc.async_disconnect()
        self.assertTrue(writer.close.called)
        self.assertTrue(writer.wait_closed.called)
        self.assertFalse(hc.is_connected)
        self.assertEqual(hc.instance, instance)

    async def test_instance_switch_causes_disconnect_if_refresh_fails(self):
        """Test that an instance must get a full refresh or it will disconnect."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        instance = 1
        switched = {
            "command": "instance-switchTo",
            "info": {"instance": instance},
            "success": True,
            "tan": 0,
        }
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)
        self._add_expected_reads(
            reader,
            reads=[
                self._to_json_line(switched),
                "THIS IS NOT A VALID SERVERINFO AND SHOULD CAUSE A DISCONNECT" + "\n",
            ],
        )

        self.assertFalse(writer.close.called)
        self.assertFalse(writer.wait_closed.called)

        await hc._async_manage_connection_once()

        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)
        self.assertFalse(hc.is_connected)

        self.assertTrue(writer.close.called)
        self.assertTrue(writer.wait_closed.called)

    async def test_is_on(self):
        """Test the client reports correctly on whether components are on."""
        (_, _, hc) = await self._create_and_test_basic_connected_client()
        with open(self._get_test_filepath(JSON_FILENAME_SERVERINFO_RESPONSE)) as fh:
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

    async def test_update_component(self):
        """Test updating components."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        # === Verify flipping a component.
        component = {
            "command": "components-update",
            "data": {"enabled": False, "name": "SMOOTHING"},
        }

        self.assertTrue(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))
        self._add_expected_reads(reader, reads=[self._to_json_line(component)])
        await hc._async_manage_connection_once()
        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))

        # === Verify a component change where the component name is not existing.
        component_name = "NOT_EXISTING"
        component = {
            "command": "components-update",
            "data": {"enabled": True, "name": component_name},
        }

        self.assertFalse(hc.is_on(components=[component_name]))
        self._add_expected_reads(reader, reads=[self._to_json_line(component)])
        await hc._async_manage_connection_once()
        self.assertTrue(hc.is_on(components=[component_name]))

        self._verify_reader(reader)

    async def test_update_adjustment(self):
        """Test updating adjustments."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        adjustment = {
            "command": "adjustment-update",
            "data": [{"brightness": 25}],
        }

        self.assertEqual(hc.adjustment[0]["brightness"], 83)
        self._add_expected_reads(reader, reads=[self._to_json_line(adjustment)])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.adjustment[0]["brightness"], 25)

    async def test_update_effect_list(self):
        """Test updating effect list."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
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

        effects = {
            "command": "effects-update",
            "data": [effect],
        }
        self.assertEqual(len(hc.effects), 39)
        self._add_expected_reads(reader, reads=[self._to_json_line(effects)])
        await hc._async_manage_connection_once()
        self.assertEqual(len(hc.effects), 1)
        self.assertEqual(hc.effects[0], effect)

    async def test_update_priorities(self):
        """Test updating priorities."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

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
        priorities_command = {
            "command": "priorities-update",
            "data": {"priorities": priorities, "priorities_autoselect": False},
        }

        self.assertEqual(len(hc.priorities), 2)
        self.assertTrue(hc.priorities_autoselect)
        self.assertEqual(hc.visible_priority["priority"], 240)
        self._add_expected_reads(reader, reads=[self._to_json_line(priorities_command)])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.priorities, priorities)
        self.assertEqual(hc.visible_priority, priorities[0])
        self.assertFalse(hc.priorities_autoselect)

        priorities_command = {
            "command": "priorities-update",
            "data": {"priorities": [], "priorities_autoselect": True},
        }

        self._add_expected_reads(reader, reads=[self._to_json_line(priorities_command)])
        await hc._async_manage_connection_once()
        self.assertIsNone(hc.visible_priority)
        self.assertTrue(hc.priorities_autoselect)

    async def test_update_instances(self):
        """Test updating instances."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        instances = [
            {"instance": 0, "running": True, "friendly_name": "Test instance 0"},
            {"instance": 1, "running": True, "friendly_name": "Test instance 1"},
            {"instance": 2, "running": True, "friendly_name": "Test instance 2"},
        ]

        instances_command = {
            "command": "instance-update",
            "data": instances,
        }

        self.assertEqual(len(hc.instances), 2)
        self._add_expected_reads(reader, reads=[self._to_json_line(instances_command)])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.instances, instances)

        # Switch to instance 1
        instance = 1
        switched = {
            "command": "instance-switchTo",
            "info": {"instance": instance},
            "success": True,
            "tan": 0,
        }
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)
        self._add_expected_reads(reader, reads=[self._to_json_line(switched)])
        self._add_expected_reads_from_files(
            reader, filenames=[JSON_FILENAME_SERVERINFO_RESPONSE]
        )

        await hc._async_manage_connection_once()
        self.assertEqual(hc.instance, instance)

        # Now update instances again to exclude instance 1 (it should reset to 0).
        instances = [
            {"instance": 0, "running": True, "friendly_name": "Test instance 0"},
            {"instance": 1, "running": False, "friendly_name": "Test instance 1"},
            {"instance": 2, "running": True, "friendly_name": "Test instance 2"},
        ]

        instances_command = {
            "command": "instance-update",
            "data": instances,
        }

        self._add_expected_reads(reader, reads=[self._to_json_line(instances_command)])
        self._add_expected_reads_from_files(
            reader, filenames=[JSON_FILENAME_SERVERINFO_RESPONSE]
        )

        await hc._async_manage_connection_once()
        self.assertEqual(hc.instance, const.DEFAULT_INSTANCE)

    async def test_update_led_mapping_type(self):
        """Test updating LED mapping type."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        led_mapping_type = "unicolor_mean"

        led_mapping_type_command = {
            "command": "imageToLedMapping-update",
            "data": {"imageToLedMappingType": led_mapping_type},
        }

        self.assertNotEqual(hc.led_mapping_type, led_mapping_type)
        self._add_expected_reads(
            reader, reads=[self._to_json_line(led_mapping_type_command)]
        )
        await hc._async_manage_connection_once()
        self.assertEqual(hc.led_mapping_type, led_mapping_type)

    async def test_update_sessions(self):
        """Test updating sessions."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

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

        sessions_command = {
            "command": "sessions-update",
            "data": sessions,
        }

        self.assertEqual(hc.sessions, [])
        self._add_expected_reads(reader, reads=[self._to_json_line(sessions_command)])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.sessions, sessions)

    async def test_videomode(self):
        """Test updating videomode."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        videomode = "3DSBS"

        videomode_update_command = {
            "command": "videomode-update",
            "data": {"videomode": videomode},
        }

        self.assertEqual(hc.videomode, "2D")

        self._add_expected_reads(
            reader, reads=[self._to_json_line(videomode_update_command)]
        )
        await hc._async_manage_connection_once()
        self.assertEqual(hc.videomode, videomode)

    async def test_update_leds(self):
        """Test updating LEDs."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        leds = [{"hmin": 0.0, "hmax": 1.0, "vmin": 0.0, "vmax": 1.0}]

        leds_command = {"command": "leds-update", "data": {"leds": leds}}

        self.assertEqual(len(hc.leds), 254)
        self._add_expected_reads(reader, reads=[self._to_json_line(leds_command)])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.leds, leds)

    async def test_set_color(self):
        """Test controlling color."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        color_in = {
            "color": [0, 0, 255],
            "command": "color",
            "origin": "My Fancy App",
            "priority": 50,
        }

        await hc.async_set_color(**color_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(color_in)])

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

        await hc.async_set_color(**color_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(color_out)])

    async def test_set_effect(self):
        """Test controlling effect."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        effect_in = {
            "command": "effect",
            "effect": {"name": "Warm mood blobs"},
            "priority": 50,
            "origin": "My Fancy App",
        }

        await hc.async_set_effect(**effect_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(effect_in)])

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

        await hc.async_set_effect(**effect_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(effect_out)])

    async def test_set_image(self):
        """Test controlling image."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        image_in = {
            "command": "image",
            "imagedata": "VGhpcyBpcyBubyBpbWFnZSEgOik=",
            "name": "Name of Image",
            "format": "auto",
            "priority": 50,
            "duration": 5000,
            "origin": "My Fancy App",
        }

        await hc.async_set_image(**image_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(image_in)])

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

        await hc.async_set_image(**image_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(image_out)])

    async def test_clear(self):
        """Test clearing priorities."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        clear_in = {
            "command": "clear",
            "priority": 50,
        }

        await hc.async_clear(**clear_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(clear_in)])
        await hc.async_clear(priority=50)
        self._verify_expected_writes(writer, writes=[self._to_json_line(clear_in)])

    async def test_set_adjustment(self):
        """Test setting adjustment."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        adjustment_in = {"command": "adjustment", "adjustment": {"gammaRed": 1.5}}

        await hc.async_set_adjustment(**adjustment_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(adjustment_in)])
        await hc.async_set_adjustment(adjustment={"gammaRed": 1.5})
        self._verify_expected_writes(writer, writes=[self._to_json_line(adjustment_in)])

    async def test_set_led_mapping_type(self):
        """Test setting adjustment."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        led_mapping_type_in = {
            "command": "processing",
            "mappingType": "multicolor_mean",
        }

        await hc.async_set_led_mapping_type(**led_mapping_type_in)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(led_mapping_type_in)]
        )
        await hc.async_set_led_mapping_type(mappingType="multicolor_mean")
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(led_mapping_type_in)]
        )

    async def test_set_videomode(self):
        """Test setting videomode."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        videomode_in = {"command": "videomode", "videoMode": "3DTAB"}

        await hc.async_set_videomode(**videomode_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(videomode_in)])
        await hc.async_set_videomode(videoMode="3DTAB")
        self._verify_expected_writes(writer, writes=[self._to_json_line(videomode_in)])

    async def test_set_component(self):
        """Test setting component."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        componentstate = {
            "component": "LEDDEVICE",
            "state": False,
        }
        component_in = {
            "command": "componentstate",
            "componentstate": componentstate,
        }

        await hc.async_set_component(**component_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(component_in)])
        await hc.async_set_component(componentstate=componentstate)
        self._verify_expected_writes(writer, writes=[self._to_json_line(component_in)])

    async def test_set_sourceselect(self):
        """Test setting sourceselect."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        sourceselect_in = {"command": "sourceselect", "priority": 50}

        await hc.async_set_sourceselect(**sourceselect_in)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(sourceselect_in)]
        )
        await hc.async_set_sourceselect(priority=50)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(sourceselect_in)]
        )

    async def test_start_stop_switch_instance(self):
        """Test starting, stopping and switching instances."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "instance", "subcommand": "startInstance", "instance": 1}

        await hc.async_start_instance(**start_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])
        await hc.async_start_instance(instance=1)
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])

        stop_in = {"command": "instance", "subcommand": "stopInstance", "instance": 1}

        await hc.async_stop_instance(**stop_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])
        await hc.async_stop_instance(instance=1)
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])

        switch_in = {"command": "instance", "subcommand": "switchTo", "instance": 1}

        await hc.async_switch_instance(**switch_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(switch_in)])
        await hc.async_switch_instance(instance=1)
        self._verify_expected_writes(writer, writes=[self._to_json_line(switch_in)])

    async def test_start_stop_image_stream(self):
        """Test starting and stopping an image stream."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "ledcolors", "subcommand": "imagestream-start"}

        await hc.async_image_stream_start(**start_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])
        await hc.async_image_stream_start()
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])

        stop_in = {"command": "ledcolors", "subcommand": "imagestream-stop"}

        await hc.async_image_stream_stop(**stop_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])
        await hc.async_image_stream_stop()
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])

    async def test_start_stop_led_stream(self):
        """Test starting and stopping an led stream."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        start_in = {"command": "ledcolors", "subcommand": "ledstream-start"}

        await hc.async_led_stream_start(**start_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])
        await hc.async_led_stream_start()
        self._verify_expected_writes(writer, writes=[self._to_json_line(start_in)])

        stop_in = {"command": "ledcolors", "subcommand": "ledstream-stop"}

        await hc.async_led_stream_stop(**stop_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])
        await hc.async_led_stream_stop()
        self._verify_expected_writes(writer, writes=[self._to_json_line(stop_in)])

    async def test_callbacks(self):
        """Test updating components."""
        received_default_json = None
        received_component_json = None

        def default_callback(json):
            nonlocal received_default_json
            received_default_json = json

        def component_callback(json):
            nonlocal received_component_json
            received_component_json = json

        (reader, writer, hc) = await self._create_and_test_basic_connected_client(
            default_callback=default_callback,
            callbacks={"components-update": component_callback},
        )

        # === Flip a component.
        component = {
            "command": "components-update",
            "data": {"enabled": False, "name": "SMOOTHING"},
        }

        self._add_expected_reads(reader, reads=[self._to_json_line(component)])
        await hc._async_manage_connection_once()
        self.assertIsNone(received_default_json)
        self.assertEqual(received_component_json, component)

        received_component_json = None

        random_update = {
            "command": "random-update",
        }

        self._add_expected_reads(reader, reads=[self._to_json_line(random_update)])
        await hc._async_manage_connection_once()

        self.assertEqual(received_default_json, random_update)
        self.assertIsNone(received_component_json)

    async def test_is_auth_required(self):
        """Test determining if authorization is required."""
        is_auth_required = None

        def auth_callback(json):
            nonlocal is_auth_required
            is_auth_required = json[const.KEY_INFO][const.KEY_REQUIRED]

        (reader, writer, hc) = await self._create_and_test_basic_connected_client(
            callbacks={"authorize-tokenRequired": auth_callback},
        )

        auth_request = {"command": "authorize", "subcommand": "tokenRequired"}

        auth_response = {
            "command": "authorize-tokenRequired",
            "info": {"required": True},
            "success": True,
            "tan": 0,
        }

        await hc.async_is_auth_required()
        self._verify_expected_writes(writer, writes=[self._to_json_line(auth_request)])

        self._add_expected_reads(reader, reads=[self._to_json_line(auth_response)])
        await hc._async_manage_connection_once()
        self.assertTrue(is_auth_required)

    async def test_async_login(self):
        """Test setting videomode."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        token = "sekrit"
        auth_login_in = {
            "command": "authorize",
            "subcommand": "login",
            "token": token,
        }

        await hc.async_login(**auth_login_in)
        self._verify_expected_writes(writer, writes=[self._to_json_line(auth_login_in)])
        await hc.async_login(token=token)
        self._verify_expected_writes(writer, writes=[self._to_json_line(auth_login_in)])

    async def test_async_logout(self):
        """Test setting videomode."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()
        auth_logout_in = {
            "command": "authorize",
            "subcommand": "logout",
        }

        await hc.async_logout(**auth_logout_in)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(auth_logout_in)]
        )
        await hc.async_logout()
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(auth_logout_in)]
        )

        # A logout success response should cause the client to disconnect.
        auth_logout_out = {
            "command": "authorize-logout",
            "success": True,
        }
        self._add_expected_reads(reader, reads=[self._to_json_line(auth_logout_out)])
        await hc._async_manage_connection_once()
        self.assertTrue(writer.close.called)
        self.assertTrue(writer.wait_closed.called)
        self.assertFalse(hc.is_connected)
        self.assertFalse(hc.manage_connection)

    async def test_request_token(self):
        """Test requesting an auth token."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        # Test requesting a token.
        request_token_in = {
            "command": "authorize",
            "subcommand": "requestToken",
            "comment": "Test",
            "id": "T3c92",
        }

        await hc.async_request_token(**request_token_in)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(request_token_in)]
        )

        # Test requesting a token with minimal provided parameters, will cause
        # the ID to be automatically generated.
        small_request_token_in = {
            "comment": "Test",
        }

        await hc.async_request_token(**small_request_token_in)

        # Do manual verification of the write calls to ensure the ID argument
        # gets set to some random value.
        name, args, kwargs = writer.write.mock_calls[0]
        data = json.loads(args[0])
        self.assertEqual(len(data), 4)
        logging.error("%s / %s", data, small_request_token_in)
        for key in ["command", "subcommand", "comment"]:
            self.assertEqual(data[key], request_token_in[key])
        self.assertEqual(len(data[const.KEY_ID]), 5)
        writer.reset_mock()

        # Abort a request for a token.
        request_token_in["accept"] = False
        await hc.async_request_token_abort(**request_token_in)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(request_token_in)]
        )

    def test_threaded_client(self):
        """Test the threaded client."""
        # An authorize-logout should cause the listening thread to disconnect.
        auth_logout_out = {
            "command": "authorize-logout",
            "success": True,
        }

        reader = self._create_mock_reader(filenames=[JSON_FILENAME_SERVERINFO_RESPONSE])
        writer = self._create_mock_writer()

        with asynctest.mock.patch(
            "asyncio.open_connection", return_value=(reader, writer)
        ):
            hc = client.ThreadedHyperionClient(
                TEST_HOST,
                TEST_PORT,
            )
            self.assertTrue(hc.connect())

        serverinfo_request_json = self._to_json_line(SERVERINFO_REQUEST)
        self._verify_expected_writes(writer, writes=[serverinfo_request_json])

        self.assertTrue(hc.is_connected)
        self._add_expected_reads(reader, reads=[self._to_json_line(auth_logout_out)])

        hc.start()
        hc.join()

        self.assertFalse(hc.is_connected)
        self._verify_reader(reader)

    def test_threaded_client_has_correct_methods(self):
        """Verify the threaded client exports all the correct methods."""
        contents = dir(
            client.ThreadedHyperionClient(
                TEST_HOST,
                TEST_PORT,
            )
        )

        # Verify all async methods have a sync wrapped version.
        for name in contents:
            if name.startswith("async_"):
                self.assertIn(name[len("async_") :], contents)

    # TODO return code from awaits

    async def test_client_write_and_close_handles_network_issues(self):
        """Verify sending data does not throw exceptions."""
        (_, writer, hc) = await self._create_and_test_basic_connected_client()

        # Verify none of these write operations result in an exception
        # propagating to the test.

        writer.write.side_effect = ConnectionError("Write exception")
        await hc.async_image_stream_start()
        writer.write.side_effect = None

        writer.drain.side_effect = ConnectionError("Drain exception")
        await hc.async_image_stream_start()
        writer.drain.side_effect = None

        writer.close.side_effect = ConnectionError("Close exception")
        await hc.async_disconnect()
        writer.close.side_effect = None

        writer.wait_closed.side_effect = ConnectionError("Wait closed exception")
        await hc.async_disconnect()
        writer.wait_closed.side_effect = None

    async def test_client_read_handles_network_issues(self):
        """Verify sending data does not throw exceptions."""

        # Verify none of these read operations result in an exception
        # propagating to the test.

        (reader, _, hc) = await self._create_and_test_basic_connected_client()
        reader.readline.side_effect = ConnectionError("Read exception")
        await hc._async_manage_connection_once()
        reader.readline.side_effect = None

        (reader, _, hc) = await self._create_and_test_basic_connected_client()
        reader.readline.side_effect = [""]
        await hc._async_manage_connection_once()
        reader.readline.side_effect = None

    async def test_client_connect_handles_network_issues(self):
        """Verify sending data does not throw exceptions."""

        # Verify none of these connect operations result in an exception
        # propagating to the test.

        with asynctest.mock.patch(
            "asyncio.open_connection",
            side_effect=ConnectionError("Connection exception"),
        ):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, loop=self.loop)
            self.assertFalse(await hc.async_connect())
