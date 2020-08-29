#!/usr/bin/python
"""Test for the Hyperion Client."""

import asynctest
import asyncio
import json
import os
from unittest import mock
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
                mock.call.write(writes[int(call_index / 2)]),
            )
            self.assertEqual(writer.method_calls[call_index + 1], mock.call.drain)
            call_index += 2
        self.assertEqual(
            len(writer.method_calls) / 2,
            len(writes),
            msg="Incorrect number of write calls",
        )
        writer.method_calls = []

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

    async def _create_and_test_basic_connected_client(self):
        """Create a basic connected client object."""
        reader = self._create_mock_reader(filenames=[JSON_FILENAME_SERVERINFO_RESPONSE])
        writer = self._create_mock_writer()

        with asynctest.mock.patch(
            "asyncio.open_connection", return_value=(reader, writer)
        ):
            hc = client.HyperionClient(TEST_HOST, TEST_PORT, loop=self.loop)
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

        self._verify_reader(reader)
        self._verify_expected_writes(
            writer,
            writes=[
                self._to_json_line(instance_request),
                self._to_json_line(SERVERINFO_REQUEST),
            ],
        )

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
        self._add_expected_reads(reader, reads=[json.dumps(component) + "\n"])
        await hc._async_manage_connection_once()
        self.assertFalse(hc.is_on(components=[const.KEY_COMPONENTID_SMOOTHING]))

        # === Verify a component change where the component name is not existing.
        component_name = "NOT_EXISTING"
        component = {
            "command": "components-update",
            "data": {"enabled": True, "name": component_name},
        }

        self.assertFalse(hc.is_on(components=[component_name]))
        self._add_expected_reads(reader, reads=[json.dumps(component) + "\n"])
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
        self._add_expected_reads(reader, reads=[json.dumps(adjustment) + "\n"])
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
        self._add_expected_reads(reader, reads=[json.dumps(effects) + "\n"])
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
        self._add_expected_reads(reader, reads=[json.dumps(priorities_command) + "\n"])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.priorities, priorities)
        self.assertEqual(hc.visible_priority, priorities[0])
        self.assertFalse(hc.priorities_autoselect)

        priorities_command = {
            "command": "priorities-update",
            "data": {"priorities": [], "priorities_autoselect": True},
        }

        self._add_expected_reads(reader, reads=[json.dumps(priorities_command) + "\n"])
        await hc._async_manage_connection_once()
        self.assertIsNone(hc.visible_priority)
        self.assertTrue(hc.priorities_autoselect)

    async def test_update_instances(self):
        """Test updating instances."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        instances = [{"instance": 0, "running": True, "friendly_name": "Test instance"}]

        instances_command = {
            "command": "instance-update",
            "data": instances,
        }

        self.assertEqual(len(hc.instances), 2)
        self._add_expected_reads(reader, reads=[json.dumps(instances_command) + "\n"])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.instances, instances)

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
            reader, reads=[json.dumps(led_mapping_type_command) + "\n"]
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
        self._add_expected_reads(reader, reads=[json.dumps(sessions_command) + "\n"])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.sessions, sessions)

    async def test_videomode(self):
        """Test updating videomode."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        videomode = "3DSBS"

        videomode_set_command = {
            "command": "videomode",
            "videoMode": videomode,
        }

        videomode_update_command = {
            "command": "videomode-update",
            "data": {"videomode": videomode},
        }

        self.assertEqual(hc.videomode, "2D")
        await hc.async_set_videomode(videomode)
        self._verify_expected_writes(
            writer, writes=[self._to_json_line(videomode_set_command)]
        )

        self._add_expected_reads(
            reader, reads=[json.dumps(videomode_update_command) + "\n"]
        )
        await hc._async_manage_connection_once()
        self.assertEqual(hc.videomode, videomode)

    async def test_update_leds(self):
        """Test updating LEDs."""
        (reader, writer, hc) = await self._create_and_test_basic_connected_client()

        leds = [{"hmin": 0.0, "hmax": 1.0, "vmin": 0.0, "vmax": 1.0}]

        leds_command = {"command": "leds-update", "data": {"leds": leds}}

        self.assertEqual(len(hc.leds), 254)
        self._add_expected_reads(reader, reads=[json.dumps(leds_command) + "\n"])
        await hc._async_manage_connection_once()
        self.assertEqual(hc.leds, leds)

    async def test_color(self):
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

    async def test_effect(self):
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

    async def test_image(self):
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
