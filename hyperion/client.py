#!/usr/bin/python
"""Client for Hyperion servers."""

import asyncio
import json
import logging
import voluptuous as vol

from hyperion import const

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


class HyperionClient:
    """Hyperion Client."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str = None,
        instance: int = 0,
        timeout_secs: int = const.DEFAULT_CONNECTION_TIMEOUT_SECS,
        retry_secs=const.DEFAULT_CONNECTION_RETRY_DELAY,
        loop=None,
    ) -> None:
        """Initialize client."""
        _LOGGER.debug("HyperionClient initiated with: (%s:%i)", host, port)
        self._host = host
        self._port = port
        self._token = token
        self._instance = instance
        self._timeout_secs = timeout_secs
        self._retry_secs = retry_secs
        self._is_connected = False

        self._serverinfo = None

        if loop is None:
            loop = asyncio.get_event_loop()
        self._loop = loop

        self._register_api_calls()

    async def async_connect(self):
        """Connect to the Hyperion server."""
        future_streams = asyncio.open_connection(self._host, self._port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                future_streams, timeout=const.DEFAULT_CONNECTION_TIMEOUT_SECS
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            _LOGGER.debug(
                "Could not connect to (%s:%i): %s", self._host, self._port, str(exc)
            )
            return False

        _LOGGER.debug(
            "Connected to Hyperion server: (%s:%i)", self._host, self._port,
        )

        # == Request: authorize ==
        if self._token is not None:
            data = {
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_LOGIN,
                const.KEY_TOKEN: self._token,
            }
            await self._async_send_json(data)
            resp_json = await self._async_safely_read_command()
            if (
                not resp_json
                or resp_json.get(const.KEY_COMMAND) != const.KEY_AUTHORIZE_LOGIN
                or not resp_json.get(const.KEY_SUCCESS, False)
            ):
                _LOGGER.warning(
                    "Authorization failed for Hyperion (%s:%i). "
                    "Check token is valid: %s",
                    self._host,
                    self._port,
                    resp_json,
                )
                return False

        # == Request: instance ==
        if self._instance != const.DEFAULT_INSTANCE:
            data = {
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_SWITCH_TO,
                const.KEY_INSTANCE: self._instance,
            }
            await self._async_send_json(data)
            resp_json = await self._async_safely_read_command()
            if (
                not resp_json
                or resp_json.get(const.KEY_COMMAND)
                != (f"{const.KEY_INSTANCE}-{const.KEY_SWITCH_TO}")
                or not resp_json.get(const.KEY_SUCCESS, False)
            ):
                _LOGGER.warning(
                    "Changing instqnce failed for Hyperion (%s:%i): %s ",
                    self._host,
                    self._port,
                    resp_json,
                )
                return False

        # == Request: serverinfo ==
        # Request full state ('serverinfo') and subscribe to relevant
        # future updates to keep this object state accurate without the need to
        # poll.
        data = {
            const.KEY_COMMAND: const.KEY_SERVERINFO,
            const.KEY_SUBSCRIBE: [
                f"{const.KEY_ADJUSTMENT}-{const.KEY_UPDATE}",
                f"{const.KEY_COMPONENTS}-{const.KEY_UPDATE}",
                f"{const.KEY_EFFECTS}-{const.KEY_UPDATE}",
                f"{const.KEY_LEDS}-{const.KEY_UPDATE}",
                f"{const.KEY_LED_MAPPING}-{const.KEY_UPDATE}",
                f"{const.KEY_INSTANCE}-{const.KEY_UPDATE}",
                f"{const.KEY_PRIORITIES}-{const.KEY_UPDATE}",
                f"{const.KEY_SESSIONS}-{const.KEY_UPDATE}",
                f"{const.KEY_VIDEOMODE}-{const.KEY_UPDATE}",
            ],
        }

        await self._async_send_json(data)
        resp_json = await self._async_safely_read_command()
        if (
            not resp_json
            or resp_json.get(const.KEY_COMMAND) != const.KEY_SERVERINFO
            or not resp_json.get(const.KEY_INFO)
            or not resp_json.get(const.KEY_SUCCESS, False)
        ):
            _LOGGER.warning(
                "Could not load initial state for Hyperion (%s:%i): %s",
                self._host,
                self._port,
                resp_json,
            )
            return False

        self._update_full_state(resp_json[const.KEY_INFO])
        self._is_connected = True
        return True

    async def _async_close_streams(self):
        """Close streams to the Hyperion server."""
        self._writer.close()
        await self._writer.wait_closed()

    async def _async_send_json(self, request):
        """Send JSON to the server."""
        _LOGGER.debug("Send to server (%s:%i): %s", self._host, self._port, request)
        output = json.dumps(request).encode("UTF-8") + b"\n"
        self._writer.write(output)
        await self._writer.drain()

    async def _async_safely_read_command(self):
        """Safely read a command from the stream."""
        connection_error = False
        try:
            resp = await self._reader.readline()
        except ConnectionError:
            connection_error = True

        if connection_error or not resp:
            _LOGGER.warning(
                "Connection to Hyperion lost (%s:%i) ...", self._host, self._port
            )
            await self._async_close_streams()
            return None

        _LOGGER.debug("Read from server (%s:%i): %s", self._host, self._port, resp)

        try:
            resp_json = json.loads(resp)
        except json.decoder.JSONDecodeError:
            _LOGGER.warning(
                "Could not decode JSON from Hyperion (%s:%i), skipping...",
                self._host,
                self._port,
            )
            return None

        if const.KEY_COMMAND not in resp_json:
            _LOGGER.warning(
                "JSON from Hyperion (%s:%i) did not include expected '%s' "
                "parameter, skipping...",
                self._host,
                self._port,
                const.KEY_COMMAND,
            )
            return None
        return resp_json

    async def async_manage_connection_in_background_forever(self):
        """Run connection management in background task."""

        def manage_forever(self):
            while True:
                self._async_manage_connection_once()

        self._loop.create_task(manage_forever(self))

    async def _async_manage_connection_once(self):
        """Manage the bidirectional connection to the server."""
        if not self._is_connected:
            if not await self.async_connect():
                _LOGGER.warning(
                    "Could not estalish valid connection to Hyperion (%s:%i), "
                    "retrying in %i seconds...",
                    self._host,
                    self._port,
                    self._retry_secs,
                )
                await self._async_close_streams()
                await asyncio.sleep(const.DEFAULT_CONNECTION_RETRY_DELAY)
                return

        resp_json = await self._async_safely_read_command()
        if not resp_json:
            return
        command = resp_json[const.KEY_COMMAND]

        if not resp_json.get(const.KEY_SUCCESS, True):
            _LOGGER.warning(
                "Failed Hyperion (%s:%i) command: %s", self._host, self._port, resp_json
            )
        elif (
            command == f"{const.KEY_COMPONENTS}-{const.KEY_UPDATE}"
            and const.KEY_DATA in resp_json
        ):
            self._update_components(resp_json[const.KEY_DATA])
        elif (
            command == f"{const.KEY_ADJUSTMENT}-{const.KEY_UPDATE}"
            and const.KEY_DATA in resp_json
        ):
            self._update_adjustment(resp_json[const.KEY_DATA])
        elif (
            command == f"{const.KEY_EFFECTS}-{const.KEY_UPDATE}"
            and const.KEY_DATA in resp_json
        ):
            self._update_effects(resp_json[const.KEY_DATA])
        elif command == f"{const.KEY_PRIORITIES}-{const.KEY_UPDATE}":
            if const.KEY_PRIORITIES in resp_json.get(const.KEY_DATA, {}):
                self._update_priorities(resp_json[const.KEY_DATA][const.KEY_PRIORITIES])
            if const.KEY_PRIORITIES_AUTOSELECT in resp_json.get(const.KEY_DATA, {}):
                self._update_priorities_autoselect(
                    resp_json[const.KEY_DATA][const.KEY_PRIORITIES_AUTOSELECT]
                )
        elif (
            command == f"{const.KEY_INSTANCE}-{const.KEY_UPDATE}"
            and const.KEY_DATA in resp_json
        ):
            self._update_instances(resp_json[const.KEY_DATA])
            # TODO: Handle disappearing instance.
        elif (
            command == f"{const.KEY_LED_MAPPING}-{const.KEY_UPDATE}"
            and const.KEY_LED_MAPPING_TYPE in resp_json.get(const.KEY_DATA, {})
        ):
            self._update_led_mapping_type(
                resp_json[const.KEY_DATA][const.KEY_LED_MAPPING_TYPE]
            )
        elif (
            command == f"{const.KEY_SESSIONS}-{const.KEY_UPDATE}"
            and const.KEY_DATA in resp_json
        ):
            self._update_sessions(resp_json[const.KEY_DATA])
        elif (
            command == f"{const.KEY_VIDEOMODE}-{const.KEY_UPDATE}"
            and const.KEY_VIDEOMODE in resp_json.get(const.KEY_DATA, {})
        ):
            self._update_videomode(resp_json[const.KEY_DATA][const.KEY_VIDEOMODE])
        elif (
            command == f"{const.KEY_LEDS}-{const.KEY_UPDATE}"
            and const.KEY_LEDS in resp_json.get(const.KEY_DATA, {})
        ):
            self._update_leds(resp_json[const.KEY_DATA][const.KEY_LEDS])

    @property
    def is_connected(self):
        """Return server availability."""
        return self._is_connected

    @property
    def serverinfo(self):
        """Return current serverinfo."""
        return self._serverinfo

    def _get_serverinfo_value(self, key):
        """Get a value from serverinfo structure given key."""
        if not self._serverinfo:
            return None
        return self._serverinfo.get(key)

    def _update_full_state(self, state):
        """Update full Hyperion state."""
        self._serverinfo = state

    def is_on(
        self, components=[const.KEY_COMPONENTID_ALL, const.KEY_COMPONENTID_LEDDEVICE]
    ):
        """Determine if components are on."""
        if not components:
            return False

        components_to_state = {}
        for component in self.components or []:
            name = component.get(const.KEY_NAME)
            state = component.get(const.KEY_ENABLED)
            if name is None or state is None:
                continue
            components_to_state[name] = state

        for component in components:
            if (
                component not in components_to_state
                or not components_to_state[component]
            ):
                return False
        return True

    @property
    def visible_priority(self):
        """Return the visible priority, if any."""
        # The visible priority is supposed to be the first returned by the
        # API, but due to a bug the ordering is incorrect search for it
        # instead, see:
        # https://github.com/hyperion-project/hyperion.ng/issues/964
        for priority in self.priorities or []:
            if priority.get(const.KEY_VISIBLE, False):
                return priority
        return None

    def _register_api_calls(self):
        api = [
            API_Set(
                self,
                const.KEY_VIDEOMODE,
                schema=vol.Schema(vol.In(const.KEY_VIDEOMODES)),
                set_command=lambda data: {
                    const.KEY_COMMAND: const.KEY_VIDEOMODE,
                    const.KEY_SET_VIDEOMODE: data,
                },
            ),
            API_Set(self, const.KEY_LEDS, schema=vol.Schema(list)),
            API_Set(self, const.KEY_SESSIONS, schema=vol.Schema(list)),
            API_Set(
                self,
                api_name=const.KEY_LED_MAPPING_API_NAME,
                key_name=const.KEY_LED_MAPPING_TYPE,
                schema=vol.Schema(str),
            ),
            API_Set(
                self,
                const.KEY_INSTANCE_API_NAME,
                key_name=const.KEY_INSTANCE,
                schema=vol.Schema(list),
            ),
            API_Set(self, const.KEY_EFFECTS, schema=vol.Schema(list)),
            API_Set(self, const.KEY_ADJUSTMENT, schema=vol.Schema([dict])),
            API_Set(self, const.KEY_PRIORITIES, schema=vol.Schema([])),
            API_Set(self, const.KEY_PRIORITIES_AUTOSELECT, schema=vol.Schema(bool)),
            Component_API_Set(
                self,
                const.KEY_COMPONENTS,
                schema=vol.Schema({const.KEY_ENABLED: bool, const.KEY_NAME: str}),
            ),
        ]

        for api_set in api:
            api_set.register()


class API_Set:
    """API endpoint set."""

    def __init__(self, client, api_name, key_name=None, schema=None, set_command=None):
        """Initialize API endpoint set."""
        self._client = client
        self._api_name = api_name
        self._key_name = key_name or api_name
        self._schema = schema
        self._set_command = set_command

    def register(self):
        """Register the API endpoints with the client class."""
        setattr(
            self._client.__class__, self._api_name, property(lambda _: self._accessor())
        )
        setattr(
            self._client.__class__,
            "_update_" + self._api_name,
            lambda _, value: self._updater(value),
        )
        if self._set_command:
            setattr(
                self._client.__class__,
                "async_set_" + self._api_name,
                lambda _, value: self._async_setter(value),
            )

    def _accessor(self):
        """Access the  data."""
        return self._client._get_serverinfo_value(self._key_name)

    async def _async_setter(self, value):
        """Async set the data."""
        try:
            if self._schema and self._schema(value):
                await self._client._async_send_json(self._set_command(value))
        except vol.Error:
            logging.warning(
                "Attempt to set invalid value for '%s': %s", self._key_name, value
            )

    def _updater(self, value):
        """Subscription updater."""
        logging.error(
            "schema %s, value %s %s", repr(self._schema), type(value), repr(value)
        )
        try:
            if self._client._serverinfo:
                if self._schema:
                    value = self._schema(value)
                self._client._serverinfo[self._key_name] = value
        except vol.Error:
            logging.warning(
                "Invalid value received for '%s': %s", self._key_name, value
            )


class Component_API_Set(API_Set):
    """Specialized API set to cope with component incremental updates."""

    def __init__(self, client, api_name, key_name=None, schema=None, set_command=None):
        """Construct Component API Set."""
        super().__init__(
            client, api_name, key_name=key_name, schema=schema, set_command=set_command
        )

    def _updater(self, incremental_component):
        """Update the components with an incremental update."""
        if not self._client._serverinfo:
            return
        if self._schema:
            incremental_component = self._schema(incremental_component)
        logging.error("moo: %s", str(incremental_component))
        new_components = self._client._serverinfo.get(const.KEY_COMPONENTS, [])
        for component in new_components:
            if (
                const.KEY_NAME not in component
                or component[const.KEY_NAME] != incremental_component[const.KEY_NAME]
            ):
                continue
            # Update component in place.
            component.clear()
            component.update(incremental_component)
            break
        else:
            new_components.append(incremental_component)
