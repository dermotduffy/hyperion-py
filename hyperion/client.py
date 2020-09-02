#!/usr/bin/python
"""Client for Hyperion servers."""

import asyncio
import inspect
import json
import logging
import random
import string
import threading

from hyperion import const

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


class HyperionClient:
    """Hyperion Client."""

    def __init__(
        self,
        host: str,
        port: int,
        default_callback: callable = None,
        callbacks: list = None,
        token: str = None,
        instance: int = 0,
        origin: str = const.DEFAULT_ORIGIN,
        timeout_secs: int = const.DEFAULT_CONNECTION_TIMEOUT_SECS,
        retry_secs=const.DEFAULT_CONNECTION_RETRY_DELAY,
        loop=None,
    ) -> None:
        """Initialize client."""
        _LOGGER.debug("HyperionClient initiated with: (%s:%i)", host, port)
        self._default_callback = default_callback
        self._callbacks = callbacks or {}

        self._host = host
        self._port = port
        self._token = token
        self._instance = instance
        self._origin = origin
        self._timeout_secs = timeout_secs
        self._retry_secs = retry_secs
        self._is_connected = False
        self._loop = loop or asyncio.get_event_loop()

        self._serverinfo = None

        self._manage_connection = True
        self._manage_connection_task = None
        self._reader = None
        self._writer = None

    # ===================
    # || Networking    ||
    # ===================

    @property
    def is_connected(self):
        """Return server availability."""
        return self._is_connected

    @property
    def instance(self):
        """Return server instance."""
        return self._instance

    @property
    def manage_connection(self):
        """Whether the client is actively managing the connection."""
        return self._manage_connection

    async def async_connect(self, *args, **kwargs):
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
            "Connected to Hyperion server: (%s:%i)",
            self._host,
            self._port,
        )

        # == Request: authorize ==
        if self._token is not None:
            await self.async_login(token=self._token)
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
            await self.async_switch_instance(instance=self._instance)
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

        if not await self._refresh_serverinfo():
            return False

        self._manage_connection = True
        self._is_connected = True
        return True

    async def _refresh_serverinfo(self):
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
                "Could not load serverinfo state for Hyperion (%s:%i): %s",
                self._host,
                self._port,
                resp_json,
            )
            return False

        self._update_serverinfo(resp_json[const.KEY_INFO])
        return True

    async def _async_disconnect_internal(self):
        """Close streams to the Hyperion server. Will be re-established."""
        self._is_connected = False
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except ConnectionError as exc:
            _LOGGER.warning(
                "Could not close connection cleanly for Hyperion (%s:%i): %s",
                self._host,
                self._port,
                str(exc),
            )
            return False
        return True

    async def async_disconnect(self, *args, **kwargs):
        """Close streams to the Hyperion server. Do not re-establish."""
        result = await self._async_disconnect_internal()
        self._manage_connection = False
        return result

    async def _async_send_json(self, request):
        """Send JSON to the server."""
        _LOGGER.debug("Send to server (%s:%i): %s", self._host, self._port, request)
        output = json.dumps(request, sort_keys=True).encode("UTF-8") + b"\n"
        try:
            self._writer.write(output)
            await self._writer.drain()
        except ConnectionError as exc:
            _LOGGER.warning(
                "Could not write data for Hyperion (%s:%i): %s",
                self._host,
                self._port,
                str(exc),
            )
            return False
        return True

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
            await self._async_disconnect_internal()
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

    async def run(self):
        """Run connection management in background task."""

        async def manage(self):
            while self._manage_connection:
                await self._async_manage_connection_once()

        self._manage_connection_task = self._loop.create_task(manage(self))

    async def _change_instance(self, instance):
        if not await self._refresh_serverinfo():
            _LOGGER.warning(
                "Could not reload state after instance change on "
                "(%s:%i, instance %i), must disconnect..."
                % (self._host, self._port, instance)
            )
            await self._async_disconnect_internal()
            return False
        else:
            self._instance = instance
        return True

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
                await self._async_disconnect_internal()
                await asyncio.sleep(const.DEFAULT_CONNECTION_RETRY_DELAY)
                return

        resp_json = await self._async_safely_read_command()
        if not resp_json or not self._serverinfo:
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
            self._update_component(resp_json[const.KEY_DATA])
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
            # If instances are changed, and the current instance is not listed
            # in the new instance update, then the connection is automatically
            # bumped back to instance 0 (the default).
            instances = resp_json[const.KEY_DATA]

            for instance in instances:
                if (
                    instance.get(const.KEY_INSTANCE) == self._instance
                    and instance.get(const.KEY_RUNNING) is True
                ):
                    self._update_instances(instances)
                    break
            else:
                await self._change_instance(const.DEFAULT_INSTANCE)
        elif (
            command == f"{const.KEY_INSTANCE}-{const.KEY_SWITCH_TO}"
            and resp_json.get(const.KEY_INFO, {}).get(const.KEY_INSTANCE) is not None
        ):
            # Upon connection being successfully switched to another instance,
            # the client will receive:
            #
            # {"command":"instance-switchTo","info":{"instance":1},"success":true,"tan":0}
            #
            # This is our cue to fully refresh our serverinfo so our internal
            # state is representing the correct instance.
            await self._change_instance(resp_json[const.KEY_INFO][const.KEY_INSTANCE])
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
        elif command == f"{const.KEY_AUTHORIZE_LOGOUT}":
            await self.async_disconnect()

        if command in self._callbacks:
            self._callbacks[command](resp_json)
        elif self._default_callback is not None:
            self._default_callback(resp_json)

    # ==================
    # || Helper calls ||
    # ==================

    @property
    def id(self):
        """Return an ID representing this Hyperion client."""
        return "%s:%i-%i" % (self._host, self._port, self._instance)

    def _set_data(self, data, hard=None, soft=None):
        output = soft or {}
        output.update(data)
        output.update(hard or {})
        return output

    # =============================
    # || Authorization API calls ||
    # =============================

    # ================================================================================
    # ** Authorization Check **
    # https://docs.hyperion-project.org/en/json/Authorization.html#authorization-check
    # ================================================================================

    async def async_is_auth_required(self, *args, **kwargs):
        """Determine if authorization is required."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_TOKEN_REQUIRED,
            },
        )
        return await self._async_send_json(data)

    # =============================================================================
    # ** Login **
    # https://docs.hyperion-project.org/en/json/Authorization.html#login-with-token
    # =============================================================================

    async def async_login(self, *args, **kwargs):
        """Login with token."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_LOGIN,
            },
        )
        return await self._async_send_json(data)

    # =============================================================================
    # ** Logout **
    # https://docs.hyperion-project.org/en/json/Authorization.html#logout
    # =============================================================================

    async def async_logout(self, *args, **kwargs):
        """Logout."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_LOGOUT,
            },
        )
        return await self._async_send_json(data)

    # ============================================================================
    # ** Request Token **
    # https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token
    # ============================================================================

    async def async_request_token(self, *args, **kwargs):
        """Request an authorization token.

        The user will accept/deny the token request on the Web UI.
        """
        random_token = "".join(
            random.choice(string.ascii_letters + string.digits) for i in range(0, 5)
        )

        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_REQUEST_TOKEN,
            },
            soft={const.KEY_ID: random_token},
        )
        return await self._async_send_json(data)

    async def async_request_token_abort(self, *args, **kwargs):
        """Abort a request for an authorization token."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_REQUEST_TOKEN,
                const.KEY_ACCEPT: False,
            },
        )
        return await self._async_send_json(data)

    # ====================
    # || Data API calls ||
    # ====================

    # ================
    # ** Adjustment **
    # ================

    @property
    def adjustment(self):
        """Return adjustment."""
        return self._get_serverinfo_value(const.KEY_ADJUSTMENT)

    def _update_adjustment(self, adjustment):
        """Update adjustment."""
        if (
            type(adjustment) != list
            or len(adjustment) != 1
            or type(adjustment[0]) != dict
        ):
            return
        self._serverinfo[const.KEY_ADJUSTMENT] = adjustment

    async def async_set_adjustment(self, *args, **kwargs):
        """Request that a color be set."""
        data = self._set_data(kwargs, hard={const.KEY_COMMAND: const.KEY_ADJUSTMENT})
        return await self._async_send_json(data)

    # =====================================================================
    # ** Clear **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#clear
    # =====================================================================

    async def async_clear(self, *args, **kwargs):
        """Request that a priority be cleared."""
        data = self._set_data(kwargs, hard={const.KEY_COMMAND: const.KEY_CLEAR})
        return await self._async_send_json(data)

    # =====================================================================
    # ** Color **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#set-color
    # =====================================================================

    async def async_set_color(self, *args, **kwargs):
        """Request that a color be set."""
        data = self._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_COLOR},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    # ==================================================================================
    # ** Component **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#components
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#component-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#control-components
    # ==================================================================================

    @property
    def components(self):
        """Return components."""
        return self._get_serverinfo_value(const.KEY_COMPONENTS)

    def _update_component(self, new_component):
        """Update full Hyperion state."""
        if type(new_component) != dict or const.KEY_NAME not in new_component:
            return
        new_components = self._serverinfo.get(const.KEY_COMPONENTS, [])
        for component in new_components:
            if (
                const.KEY_NAME not in component
                or component[const.KEY_NAME] != new_component[const.KEY_NAME]
            ):
                continue
            # Update component in place.
            component.clear()
            component.update(new_component)
            break
        else:
            new_components.append(new_component)

    async def async_set_component(self, *args, **kwargs):
        """Request that a color be set."""
        data = self._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_COMPONENTSTATE}
        )
        return await self._async_send_json(data)

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

    # ==================================================================================
    # ** Effects **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#effect-list
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#effects-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#set-effect
    # ==================================================================================

    @property
    def effects(self):
        """Return effects."""
        return self._get_serverinfo_value(const.KEY_EFFECTS)

    def _update_effects(self, effects):
        """Update effects."""
        if type(effects) != list:
            return
        self._serverinfo[const.KEY_EFFECTS] = effects

    async def async_set_effect(self, *args, **kwargs):
        """Request that an effect be set."""
        data = self._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_EFFECT},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    # =================================================================================
    # ** Image **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#set-image
    # =================================================================================

    async def async_set_image(self, *args, **kwargs):
        """Request that an image be set."""
        data = self._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_IMAGE},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    # ================================================================================
    # ** Image Streaming **
    # Update: https://docs.hyperion-project.org/en/json/Control.html#live-image-stream
    # Set: https://docs.hyperion-project.org/en/json/Control.html#live-image-stream
    # ================================================================================

    async def async_image_stream_start(self, *args, **kwargs):
        """Request a live image stream to start."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_IMAGE_STREAM_START,
            },
        )
        return await self._async_send_json(data)

    async def async_image_stream_stop(self, *args, **kwargs):
        """Request a live image stream to stop."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_IMAGE_STREAM_STOP,
            },
        )
        return await self._async_send_json(data)

    # =================================================================================
    # ** Instances **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#instance
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#instance-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#control-instances
    # =================================================================================

    @property
    def instances(self):
        """Return instances."""
        return self._get_serverinfo_value(const.KEY_INSTANCE)

    def _update_instances(self, instances):
        """Update instances."""
        if type(instances) != list:
            return
        self._serverinfo[const.KEY_INSTANCE] = instances

    async def async_start_instance(self, *args, **kwargs):
        """Start an instance."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_START_INSTANCE,
            },
        )
        return await self._async_send_json(data)

    async def async_stop_instance(self, *args, **kwargs):
        """Stop an instance."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_STOP_INSTANCE,
            },
        )
        return await self._async_send_json(data)

    async def async_switch_instance(self, *args, **kwargs):
        """Stop an instance."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_SWITCH_TO,
            },
        )
        return await self._async_send_json(data)

    # =============================================================================
    # ** LEDs **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#leds
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#leds-updates
    # =============================================================================

    @property
    def leds(self):
        """Return LEDs."""
        return self._get_serverinfo_value(const.KEY_LEDS)

    def _update_leds(self, leds):
        """Update LEDs."""
        if type(leds) != list:
            return
        self._serverinfo[const.KEY_LEDS] = leds

    # ====================================================================================
    # ** LED Mapping **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#led-mapping
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#led-mapping-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#led-mapping
    # ====================================================================================

    @property
    def led_mapping_type(self):
        """Return LED mapping type."""
        return self._get_serverinfo_value(const.KEY_LED_MAPPING_TYPE)

    def _update_led_mapping_type(self, led_mapping_type):
        """Update LED mapping  type."""
        if type(led_mapping_type) != str:
            return
        self._serverinfo[const.KEY_LED_MAPPING_TYPE] = led_mapping_type

    async def async_set_led_mapping_type(self, *args, **kwargs):
        """Request the LED mapping type be set."""
        data = self._set_data(kwargs, hard={const.KEY_COMMAND: const.KEY_PROCESSING})
        return await self._async_send_json(data)

    # ===================================================================================
    # ** Live LED Streaming **
    # Update: https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream
    # Set: https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream
    # ====================================================================================

    async def async_led_stream_start(self, *args, **kwargs):
        """Request a live led stream to start."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_LED_STREAM_START,
            },
        )
        return await self._async_send_json(data)

    async def async_led_stream_stop(self, *args, **kwargs):
        """Request a live led stream to stop."""
        data = self._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_LED_STREAM_STOP,
            },
        )
        return await self._async_send_json(data)

    # =================================================================================
    # ** Priorites **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#priorities
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#priority-updates
    # =================================================================================

    @property
    def priorities(self):
        """Return priorites."""
        return self._get_serverinfo_value(const.KEY_PRIORITIES)

    def _update_priorities(self, priorities):
        """Update priorites."""
        if type(priorities) != list:
            return
        self._serverinfo[const.KEY_PRIORITIES] = priorities

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

    # ======================================================================================================
    # ** Priorites Autoselect **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#priorities-selection-auto-manual
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#priority-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#source-selection
    # ======================================================================================================

    @property
    def priorities_autoselect(self):
        """Return priorites."""
        return self._get_serverinfo_value(const.KEY_PRIORITIES_AUTOSELECT)

    def _update_priorities_autoselect(self, priorities_autoselect):
        """Update priorites."""
        if type(priorities_autoselect) != bool:
            return
        self._serverinfo[const.KEY_PRIORITIES_AUTOSELECT] = priorities_autoselect

    async def async_set_sourceselect(self, *args, **kwargs):
        """Request the sourceselect be set."""
        data = self._set_data(kwargs, hard={const.KEY_COMMAND: const.KEY_SOURCESELECT})
        return await self._async_send_json(data)

    # ================================================================================
    # ** Sessions **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#sessions
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#session-updates
    # ================================================================================

    @property
    def sessions(self):
        """Return sessions."""
        return self._get_serverinfo_value(const.KEY_SESSIONS)

    def _update_sessions(self, sessions):
        """Update sessions."""
        if type(sessions) != list:
            return
        self._serverinfo[const.KEY_SESSIONS] = sessions

    # =====================================================================
    # ** Serverinfo (full state) **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html
    # =====================================================================

    @property
    def serverinfo(self):
        """Return current serverinfo."""
        return self._serverinfo

    def _update_serverinfo(self, state):
        """Update full Hyperion state."""
        self._serverinfo = state

    def _get_serverinfo_value(self, key):
        """Get a value from serverinfo structure given key."""
        if not self._serverinfo:
            return None
        return self._serverinfo.get(key)

    # ==================================================================================
    # ** Videomode **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#video-mode
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#videomode-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#video-mode
    # ==================================================================================

    @property
    def videomode(self):
        """Return videomode."""
        return self._get_serverinfo_value(const.KEY_VIDEOMODE)

    def _update_videomode(self, videomode):
        """Update videomode."""
        if self._serverinfo:
            self._serverinfo[const.KEY_VIDEOMODE] = videomode

    async def async_set_videomode(self, *args, **kwargs):
        """Request the LED mapping type be set."""
        data = self._set_data(kwargs, hard={const.KEY_COMMAND: const.KEY_VIDEOMODE})
        return await self._async_send_json(data)


class ThreadedHyperionClient(HyperionClient, threading.Thread):
    """Hyperion Client that runs in a dedicated thread."""

    def __init__(
        self,
        host: str,
        port: int,
        default_callback: callable = None,
        callbacks: list = None,
        token: str = None,
        instance: int = 0,
        origin: str = const.DEFAULT_ORIGIN,
        timeout_secs: int = const.DEFAULT_CONNECTION_TIMEOUT_SECS,
        retry_secs=const.DEFAULT_CONNECTION_RETRY_DELAY,
    ) -> None:
        """Initialize client."""
        loop = asyncio.new_event_loop()
        threading.Thread.__init__(self)
        HyperionClient.__init__(
            self,
            host,
            port,
            default_callback=default_callback,
            callbacks=callbacks,
            token=token,
            instance=instance,
            origin=origin,
            timeout_secs=timeout_secs,
            retry_secs=retry_secs,
            loop=loop,
        )

        for name, value in inspect.getmembers(self):
            if name.startswith("async_") and inspect.ismethod(value):
                new_name = name[len("async_") :]
                self._register_sync_call(new_name, value)

    def _register_sync_call(self, new_name, value):
        """Register a sync version of an async call."""
        setattr(
            self,
            new_name,
            lambda *args, **kwargs: self._async_wrapper(value, *args, **kwargs),
        )

    def _async_wrapper(self, coro, *args, **kwargs):
        """Convert a async call to synchronous by running it in the local event loop."""
        task = coro(*args, **kwargs)
        done, _ = asyncio.get_event_loop().run_until_complete(asyncio.wait([task]))
        if done:
            return done.pop().result()

    def run(self):
        """Run connection management in this thread."""

        async def manage(self):
            while self._manage_connection:
                await self._async_manage_connection_once()

        self._manage_connection_task = self._loop.create_task(manage(self))
        self._loop.run_until_complete(asyncio.wait([self._manage_connection_task]))
