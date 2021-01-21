#!/usr/bin/python
"""Client for Hyperion servers."""

import asyncio
import collections
import copy
import functools
import inspect
import json
import logging
import random
import string
import threading
from types import TracebackType

# pylint: disable=unused-import
from typing import (
    cast,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    Iterable,
    List,
    Optional,
    Type,
    Union,
)

from hyperion import const

_LOGGER = logging.getLogger(__name__)

HyperionCallback = Union[
    Callable[[Dict[str, Any]], Awaitable[None]],
    Callable[[Dict[str, Any]], None],
]


class HyperionError(Exception):
    """Baseclass for all Hyperion exceptions."""


class HyperionClientTanNotAvailable(HyperionError):
    """An exception indicating the requested tan is not available."""


class HyperionClientState:
    """Class representing the Hyperion client state."""

    def __init__(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Initialize state object."""
        self._state: Dict[str, Any] = state or {}
        self._dirty: bool = False

    @property
    def dirty(self) -> bool:
        """Return whether or state has been modified."""
        return self._dirty

    @dirty.setter
    def dirty(self, val: bool) -> None:
        self._dirty = val

    def get(self, key: str) -> Any:
        """Retrieve a state element."""
        return self._state.get(key)

    def set(self, key: str, new_value: Any) -> None:
        """Set a new state value."""
        old_value = self.get(key)
        if old_value != new_value:
            self._state[key] = new_value
            self._dirty = True

    def update(self, new_values: Dict[str, Any]) -> None:
        """Update the state with a dict of values."""
        for key in new_values:
            self.set(key, new_values[key])

    def get_all(self) -> Dict[str, Any]:
        """Get a copy of all the state values."""
        return copy.copy(self._state)


class HyperionClient:
    """Hyperion Client."""

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        host: str,
        port: int = const.DEFAULT_PORT_JSON,
        default_callback: Optional[
            Union[HyperionCallback, Iterable[HyperionCallback]]
        ] = None,
        callbacks: Optional[
            Dict[str, Union[HyperionCallback, Iterable[HyperionCallback]]]
        ] = None,
        token: Optional[str] = None,
        instance: int = const.DEFAULT_INSTANCE,
        origin: str = const.DEFAULT_ORIGIN,
        timeout_secs: float = const.DEFAULT_TIMEOUT_SECS,
        retry_secs: int = const.DEFAULT_CONNECTION_RETRY_DELAY_SECS,
        raw_connection: bool = False,
    ) -> None:
        """Initialize client."""
        _LOGGER.debug("HyperionClient initiated with: (%s:%i)", host, port)

        self._callbacks: Dict[str, List[HyperionCallback]] = {}
        self.set_callbacks(callbacks or {})

        self._default_callback: List[HyperionCallback] = []
        self.set_default_callback(default_callback or [])

        self._host = host
        self._port = port
        self._token = token
        self._target_instance = instance
        self._origin = origin
        self._timeout_secs = timeout_secs
        self._retry_secs = retry_secs
        self._raw_connection = raw_connection

        self._serverinfo: Optional[Dict[str, Any]] = None

        self._receive_task: Optional[asyncio.Task[None]] = None
        self._maintenance_task: Optional[asyncio.Task[None]] = None
        self._maintenance_event: asyncio.Event = asyncio.Event()

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

        # Start tan @ 1, as the zeroth tan is used by default.
        self._tan_cv = asyncio.Condition()
        self._tan_counter = 1
        self._tan_responses: Dict[
            int, Optional[Dict[str, Any]]
        ] = collections.OrderedDict()

        self._client_state: HyperionClientState = HyperionClientState(
            state={
                const.KEY_CONNECTED: False,
                const.KEY_LOGGED_IN: False,
                const.KEY_INSTANCE: None,
                const.KEY_LOADED_STATE: False,
            }
        )

    async def __aenter__(self) -> Optional["HyperionClient"]:
        """Enter context manager and connect the client."""
        result = await self.async_client_connect()
        return self if result else None

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Leave context manager and disconnect the client."""
        await self.async_client_disconnect()

    async def _client_state_reset(self) -> None:
        self._client_state.update(
            {
                const.KEY_CONNECTED: False,
                const.KEY_LOGGED_IN: False,
                const.KEY_INSTANCE: None,
                const.KEY_LOADED_STATE: False,
            }
        )

    # ===================
    # || Callbacks     ||
    # ===================

    @classmethod
    def _set_or_add_callbacks(
        cls,
        callbacks: Optional[Union[HyperionCallback, Iterable[HyperionCallback]]],
        add: bool,
        target: List[HyperionCallback],
    ) -> None:
        """Set or add a single or list of callbacks."""
        if callbacks is not None and not isinstance(
            callbacks, collections.abc.Iterable
        ):
            callbacks = [callbacks]
        if not add:
            target.clear()
        if callbacks is not None:
            target.extend(callbacks)

    @classmethod
    def _remove_callbacks(
        cls,
        callbacks: Union[HyperionCallback, Iterable[HyperionCallback]],
        target: List[HyperionCallback],
    ) -> None:
        """Set or add a single or list of callbacks."""
        if not callbacks:
            return
        if not isinstance(callbacks, collections.abc.Iterable):
            callbacks = [callbacks]

        for callback in callbacks:
            if callback in target:
                target.remove(callback)

    def set_callbacks(
        self,
        callbacks: Optional[
            Dict[str, Union[HyperionCallback, Iterable[HyperionCallback]]]
        ],
    ) -> None:
        """Set update callbacks."""
        if not callbacks:
            self._callbacks = {}
            return
        for name, value in callbacks.items():
            HyperionClient._set_or_add_callbacks(
                value, False, self._callbacks.setdefault(name, [])
            )

    def add_callbacks(
        self,
        callbacks: Dict[str, Union[HyperionCallback, Iterable[HyperionCallback]]],
    ) -> None:
        """Add update callbacks."""
        if not callbacks:
            return
        for name, value in callbacks.items():
            HyperionClient._set_or_add_callbacks(
                value, True, self._callbacks.setdefault(name, [])
            )

    def remove_callbacks(
        self,
        callbacks: Dict[str, Union[HyperionCallback, Iterable[HyperionCallback]]],
    ) -> None:
        """Add update callbacks."""
        if not callbacks:
            return
        for name, value in callbacks.items():
            if name not in self._callbacks:
                continue
            HyperionClient._remove_callbacks(value, self._callbacks[name])

    def set_default_callback(
        self,
        default_callback: Optional[Union[HyperionCallback, Iterable[HyperionCallback]]],
    ) -> None:
        """Set the default callbacks."""
        HyperionClient._set_or_add_callbacks(
            default_callback, False, self._default_callback
        )

    def add_default_callback(
        self,
        default_callback: Union[HyperionCallback, Iterable[HyperionCallback]],
    ) -> None:
        """Set the default callbacks."""
        HyperionClient._set_or_add_callbacks(
            default_callback, True, self._default_callback
        )

    def remove_default_callback(
        self,
        default_callback: Union[HyperionCallback, Iterable[HyperionCallback]],
    ) -> None:
        """Set the default callbacks."""
        HyperionClient._remove_callbacks(default_callback, self._default_callback)

    async def _call_callbacks(self, command: str, arg: Dict[str, Any]) -> None:
        """Call the relevant callbacks for the given command."""
        callbacks = self._callbacks.get(command, self._default_callback)
        for callback in callbacks:
            if inspect.iscoroutinefunction(callback):
                await cast(Awaitable[None], callback(arg))
            else:
                callback(arg)

    # ===================
    # || Networking    ||
    # ===================

    @property
    def is_connected(self) -> bool:
        """Return server availability."""
        return bool(self._client_state.get(const.KEY_CONNECTED))

    @property
    def is_logged_in(self) -> bool:
        """Return whether the client is logged in."""
        return bool(self._client_state.get(const.KEY_LOGGED_IN))

    @property
    def instance(self) -> Optional[int]:
        """Return server instance."""
        return cast(Optional[int], self._client_state.get(const.KEY_INSTANCE))

    @property
    def target_instance(self) -> int:
        """Return server target instance."""
        return self._target_instance

    @property
    def has_loaded_state(self) -> bool:
        """Return whether the client has loaded state."""
        return bool(self._client_state.get(const.KEY_LOADED_STATE))

    @property
    def client_state(self) -> Dict[str, Any]:
        """Return client state."""
        return self._client_state.get_all()

    async def async_client_connect(self) -> bool:
        """Connect to the Hyperion server."""

        if self._writer:
            return True

        future_streams = asyncio.open_connection(self._host, self._port)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                future_streams, timeout=self._timeout_secs
            )
        except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
            _LOGGER.debug("Could not connect to (%s): %s", self._host_port, repr(exc))
            return False

        _LOGGER.info("Connected to Hyperion server: %s", self._host_port)

        # Start the receive task to process inbound data from the server.
        await self._await_or_stop_task(self._receive_task, stop_task=True)
        self._receive_task = asyncio.create_task(self._receive_task_loop())

        await self._client_state_reset()
        self._client_state.update(
            {const.KEY_CONNECTED: True, const.KEY_INSTANCE: const.DEFAULT_INSTANCE}
        )
        await self._call_client_state_callback_if_necessary()

        if not self._raw_connection:
            if (
                not self._client_state.get(const.KEY_LOGGED_IN)
                and not await self.async_client_login()
            ):
                await self.async_client_disconnect()
                return False

            if (
                not self._client_state.get(const.KEY_INSTANCE)
                and not await self.async_client_switch_instance()
            ):
                await self.async_client_disconnect()
                return False

            if not self._client_state.get(
                const.KEY_LOADED_STATE
            ) and not ServerInfoResponseOK(await self.async_get_serverinfo()):
                await self.async_client_disconnect()
                return False

        # Start the maintenance task if it does not already exist.
        if not self._maintenance_task:
            self._maintenance_task = asyncio.create_task(self._maintenance_task_loop())

        return True

    async def _call_client_state_callback_if_necessary(self) -> None:
        """Call the client state callbacks if state has changed."""
        if not self._client_state.dirty:
            return
        data = HyperionClient._set_data(
            self._client_state.get_all(),
            hard={
                const.KEY_COMMAND: f"{const.KEY_CLIENT}-{const.KEY_UPDATE}",
            },
        )
        await self._call_callbacks(str(data[const.KEY_COMMAND]), data)
        self._client_state.dirty = False

    async def async_client_login(self) -> bool:
        """Log the client in if a token is provided."""
        if self._token is None:
            self._client_state.set(const.KEY_LOGGED_IN, True)
            await self._call_client_state_callback_if_necessary()
            return True
        return bool(LoginResponseOK(await self.async_login(token=self._token)))

    async def async_client_switch_instance(self) -> bool:
        """Select an instance the user has specified."""
        if (
            self._client_state.get(const.KEY_INSTANCE) is None
            and self._target_instance == const.DEFAULT_INSTANCE
        ) or self._client_state.get(const.KEY_INSTANCE) == self._target_instance:
            self._client_state.set(const.KEY_INSTANCE, self._target_instance)
            await self._call_client_state_callback_if_necessary()
            return True

        resp_json = await self.async_switch_instance(instance=self._target_instance)
        return (
            resp_json is not None
            and bool(SwitchInstanceResponseOK(resp_json))
            and resp_json[const.KEY_INFO][const.KEY_INSTANCE] == self._target_instance
        )

    async def async_client_disconnect(self) -> bool:
        """Close streams to the Hyperion server (no reconnect)."""
        # Cancel the maintenance task to ensure the connection is not re-established.
        await self._await_or_stop_task(self._maintenance_task, stop_task=True)
        self._maintenance_task = None

        return await self._async_client_disconnect_internal()

    async def _async_client_disconnect_internal(self) -> bool:
        """Close streams to the Hyperion server (may reconnect)."""
        if not self._writer:
            return True

        error = False
        writer = self._writer
        self._writer = self._reader = None
        try:
            writer.close()
            await writer.wait_closed()
        except ConnectionError as exc:
            _LOGGER.warning(
                "Could not close connection cleanly for Hyperion (%s): %s",
                self._host_port,
                repr(exc),
            )
            error = True

        await self._client_state_reset()
        await self._call_client_state_callback_if_necessary()

        # Tell the maintenance loop it may need to reconnect.
        self._maintenance_event.set()

        receive_task = self._receive_task
        self._receive_task = None
        await self._await_or_stop_task(receive_task, stop_task=True)
        return not error

    async def _async_send_json(self, request: Dict[str, Any]) -> bool:
        """Send JSON to the server."""
        if not self._writer:
            return False

        output = json.dumps(request, sort_keys=True).encode("UTF-8") + b"\n"
        _LOGGER.debug("Send to server (%s): %s", self._host_port, output)
        try:
            self._writer.write(output)
            await self._writer.drain()
        except ConnectionError as exc:
            _LOGGER.warning(
                "Could not write data for Hyperion (%s): %s",
                self._host_port,
                repr(exc),
            )
            return False
        return True

    # pylint: disable=too-many-return-statements
    async def _async_safely_read_command(
        self, use_timeout: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Safely read a command from the stream."""
        if not self._reader:
            return None

        timeout_secs = self._timeout_secs if use_timeout else None

        try:
            future_resp = self._reader.readline()
            resp = await asyncio.wait_for(future_resp, timeout=timeout_secs)
        except ConnectionError:
            _LOGGER.warning("Connection to Hyperion lost (%s) ...", self._host_port)
            await self._async_client_disconnect_internal()
            return None
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Read from Hyperion timed out (%s), disconnecting ...", self._host_port
            )
            await self._async_client_disconnect_internal()
            return None

        if not resp:
            # If there's no writer, we have disconnected, so skip the error message
            # and additional disconnect call.
            if self._writer:
                _LOGGER.warning("Connection to Hyperion lost (%s) ...", self._host_port)
                await self._async_client_disconnect_internal()
            return None

        _LOGGER.debug("Read from server (%s): %s", self._host_port, resp)

        try:
            resp_json = json.loads(resp)
        except json.decoder.JSONDecodeError:
            _LOGGER.warning(
                "Could not decode JSON from Hyperion (%s), skipping...",
                self._host_port,
            )
            return None

        try:
            resp_json = dict(resp_json)
        except ValueError:
            _LOGGER.warning(
                "Wrong data-type received from Hyperion (%s), skipping...",
                self._host_port,
            )
            return None

        if const.KEY_COMMAND not in resp_json:
            _LOGGER.warning(
                "JSON from Hyperion (%s) did not include expected '%s' "
                "parameter, skipping...",
                self._host_port,
                const.KEY_COMMAND,
            )
            return None
        return cast(Dict[str, Any], resp_json)

    async def _maintenance_task_loop(self) -> None:
        try:
            while True:
                await self._maintenance_event.wait()
                if not self._client_state.get(const.KEY_CONNECTED):
                    if not await self.async_client_connect():
                        _LOGGER.info(
                            "Could not estalish valid connection to Hyperion (%s), "
                            "retrying in %i seconds...",
                            self._host_port,
                            self._retry_secs,
                        )
                        await self._async_client_disconnect_internal()
                        await asyncio.sleep(const.DEFAULT_CONNECTION_RETRY_DELAY_SECS)
                        continue

                if self._client_state.get(const.KEY_CONNECTED):
                    self._maintenance_event.clear()

        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            # Don't log CancelledError, but do propagate it upwards.
            raise
        except Exception:
            # Make sure exceptions are logged (for testing purposes, as this is
            # in a background task).
            _LOGGER.exception(
                "Exception in Hyperion (%s) background maintenance task",
                self._host_port,
            )
            raise

    async def _receive_task_loop(self) -> None:
        """Run receive task continually."""
        while await self._async_receive_once():
            pass

    async def _await_or_stop_task(
        self, task: "Optional[asyncio.Task[Any]]", stop_task: bool = False
    ) -> bool:
        """Await task, optionally stopping it first.

        Returns True if the task is done.
        """
        if task is None:
            return False
        if stop_task:
            task.cancel()

        # Yield to the event loop, so the above cancellation can be processed.
        await asyncio.sleep(0)

        if task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False

    async def _handle_changed_instance(self, instance: int) -> None:
        """Handle when instance changes (whether this client triggered that or not)."""
        if instance == self._client_state.get(const.KEY_INSTANCE):
            return
        self._target_instance = instance
        self._client_state.update(
            {const.KEY_INSTANCE: instance, const.KEY_LOADED_STATE: False}
        )
        self._update_serverinfo(None)
        await self._call_client_state_callback_if_necessary()

    # pylint: disable=too-many-branches
    async def _async_receive_once(self) -> bool:
        """Manage the bidirectional connection to the server."""
        resp_json = await self._async_safely_read_command(use_timeout=False)
        if not resp_json:
            return False
        command = resp_json[const.KEY_COMMAND]

        if not resp_json.get(const.KEY_SUCCESS, True):
            # If it's a failed authorization call, print a specific warning
            # message.
            if command == const.KEY_AUTHORIZE_LOGIN:
                _LOGGER.warning(
                    "Authorization failed for Hyperion (%s). "
                    "Check token is valid: %s",
                    self._host_port,
                    resp_json,
                )
            else:
                _LOGGER.warning(
                    "Failed Hyperion (%s) command: %s", self._host_port, resp_json
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
            # in the new instance update, then the client should disconnect.
            instances = resp_json[const.KEY_DATA]

            for instance in instances:
                if (
                    instance.get(const.KEY_INSTANCE)
                    == self._client_state.get(const.KEY_INSTANCE)
                    and instance.get(const.KEY_RUNNING) is True
                ):
                    self._update_instances(instances)
                    break
            else:
                await self.async_client_disconnect()
        elif SwitchInstanceResponseOK(resp_json):
            # Upon connection being successfully switched to another instance,
            # the client will receive:
            #
            # {
            #     "command":"instance-switchTo",
            #     "info": {
            #         "instance": 1
            #     },
            #     "success":true,
            #     "tan":0
            # }
            #
            # This is our cue to fully refresh our serverinfo so our internal
            # state is representing the correct instance.
            await self._handle_changed_instance(
                resp_json[const.KEY_INFO][const.KEY_INSTANCE]
            )
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
            await self.async_client_disconnect()
        elif ServerInfoResponseOK(resp_json):
            self._update_serverinfo(resp_json[const.KEY_INFO])
            self._client_state.set(const.KEY_LOADED_STATE, True)
        elif LoginResponseOK(resp_json):
            self._client_state.set(const.KEY_LOGGED_IN, True)

        await self._call_callbacks(command, resp_json)
        await self._call_client_state_callback_if_necessary()
        await self._handle_response_for_caller(resp_json)
        return True

    async def _handle_response_for_caller(self, resp_json: Dict[str, Any]) -> None:
        """Handle a server response for a caller."""

        tan = resp_json.get(const.KEY_TAN)
        if tan is not None:
            async with self._tan_cv:
                if tan in self._tan_responses:
                    self._tan_responses[tan] = resp_json
                    self._tan_cv.notify_all()
                # Note: The behavior is not perfect here, in cases of an older
                # Hyperion server and a malformed request. In that case, the
                # server will return tan==0 (regardless of the input tan), and
                # so the match here will fail. This will cause the callee to
                # time out awaiting a response (or wait forever if not timeout
                # is specified). This was addressed in:
                #
                # https://github.com/hyperion-project/hyperion.ng/issues/1001 .

    # ==================
    # || Helper calls ||
    # ==================

    @property
    def _host_port(self) -> str:
        """Return a host:port string for this server."""
        return "%s:%i" % (self._host, self._port)

    @classmethod
    def _set_data(
        cls,
        data: Dict[Any, Any],
        hard: Optional[Dict[Any, Any]] = None,
        soft: Optional[Dict[Any, Any]] = None,
    ) -> Dict[Any, Any]:
        """Override the data in the dictionary selectively."""
        output = soft or {}
        output.update(data)
        output.update(hard or {})
        return output

    async def _reserve_tan_slot(self, tan: Optional[int] = None) -> int:
        """Increment and return the next tan to use."""
        async with self._tan_cv:
            if tan is None:
                # If tan is not specified, find the next available higher
                # value.
                while self._tan_counter in self._tan_responses:
                    self._tan_counter += 1
                tan = self._tan_counter
                self._tan_counter += 1
            if tan in self._tan_responses:
                raise HyperionClientTanNotAvailable(
                    "Requested tan '%i' is not available in Hyperion client (%s)"
                    % (tan, self._host_port)
                )
            self._tan_responses[tan] = None
            return tan

    async def _remove_tan_slot(self, tan: int) -> None:
        """Remove a tan slot that is no longer required."""
        async with self._tan_cv:
            if tan in self._tan_responses:
                del self._tan_responses[tan]

    async def _wait_for_tan_response(
        self, tan: int, timeout_secs: float
    ) -> Optional[Dict[str, Any]]:
        """Wait for a response to arrive."""
        await self._tan_cv.acquire()
        try:
            await asyncio.wait_for(
                self._tan_cv.wait_for(lambda: self._tan_responses.get(tan) is not None),
                timeout=timeout_secs,
            )
            return self._tan_responses[tan]
        except asyncio.TimeoutError:
            pass
        finally:
            # This should not be necessary, this function should be able to use
            # 'async with self._tan_cv', however this does not currently play nice
            # with Python 3.7/3.8 when the wait_for is canceled or times out,
            # (the condition lock is not reaquired before re-raising the exception).
            # See: https://bugs.python.org/issue39032
            if self._tan_cv.locked():
                self._tan_cv.release()
        return None

    class AwaitResponseWrapper:
        """Wrapper an async *send* coroutine and await the response."""

        def __init__(
            self, coro: Callable[..., Awaitable[bool]], timeout_secs: float = 0
        ):
            """Initialize the wrapper.

            Wait up to timeout_secs for a response. A timeout of 0
            will use the client default timeout specified in the constructor.
            A timeout of None will wait forever.
            """
            self._coro = coro
            self._timeout_secs = timeout_secs

        def _extract_timeout_secs(
            self, hyperion_client: "HyperionClient", data: Dict[str, Any]
        ) -> float:
            """Return the timeout value for a call.

            Modifies input! Removes the timeout key from the inbound data if
            present so that it is not passed on to the server. If not present,
            returns the wrapper default specified in the wrapper constructor.
            """
            # Timeout values:
            #    * None: Wait forever (default asyncio.wait_for behavior).
            #    * 0: Use the object default (self._timeout_secs)
            #    * >0: Wait that long.
            if const.KEY_TIMEOUT_SECS in data:
                timeout_secs = cast(float, data[const.KEY_TIMEOUT_SECS])
                del data[const.KEY_TIMEOUT_SECS]
                return timeout_secs
            if self._timeout_secs == 0:
                return hyperion_client._timeout_secs  # pylint: disable=protected-access
            return self._timeout_secs

        async def __call__(
            self, hyperion_client: "HyperionClient", *args: Any, **kwargs: Any
        ) -> Optional[Dict[str, Any]]:
            """Call the wrapper."""
            # The receive task should never be executing a call that uses the
            # AwaitResponseWrapper (as the response is itself handled by the receive
            # task, i.e. partial deadlock). This assertion defends against programmer
            # error in development of the client iself.
            assert asyncio.current_task() != hyperion_client._receive_task

            tan = await hyperion_client._reserve_tan_slot(kwargs.get(const.KEY_TAN))
            data = hyperion_client._set_data(kwargs, hard={const.KEY_TAN: tan})
            timeout_secs = self._extract_timeout_secs(hyperion_client, data)

            response = None
            if await self._coro(hyperion_client, *args, **data):
                response = await hyperion_client._wait_for_tan_response(
                    tan, timeout_secs
                )
            await hyperion_client._remove_tan_slot(tan)
            return response

        def __get__(
            self, instance: "HyperionClient", instancetype: Type["HyperionClient"]
        ) -> "functools.partial[Coroutine[Any, Any, Optional[Dict[str, Any]]]]":
            """Return a partial call that uses the correct 'self'."""
            # Need to ensure __call__ receives the 'correct' outer
            # 'self', which is 'instance' in this function.
            return functools.partial(self.__call__, instance)

    # =============================
    # || Authorization API calls ||
    # =============================

    # ================================================================================
    # ** Authorization Check **
    # https://docs.hyperion-project.org/en/json/Authorization.html#authorization-check
    # ================================================================================

    async def async_send_is_auth_required(self, *_: Any, **kwargs: Any) -> bool:
        """Determine if authorization is required."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_TOKEN_REQUIRED,
            },
        )
        return await self._async_send_json(data)

    async_is_auth_required = AwaitResponseWrapper(async_send_is_auth_required)

    # =============================================================================
    # ** Login **
    # https://docs.hyperion-project.org/en/json/Authorization.html#login-with-token
    # =============================================================================

    async def async_send_login(self, *_: Any, **kwargs: Any) -> bool:
        """Login with token."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_LOGIN,
            },
        )
        return await self._async_send_json(data)

    async_login = AwaitResponseWrapper(async_send_login)

    # =============================================================================
    # ** Logout **
    # https://docs.hyperion-project.org/en/json/Authorization.html#logout
    # =============================================================================

    async def async_send_logout(self, *_: Any, **kwargs: Any) -> bool:
        """Logout."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_LOGOUT,
            },
        )
        return await self._async_send_json(data)

    async_logout = AwaitResponseWrapper(async_send_logout)

    # ============================================================================
    # ** Request Token **
    # https://docs.hyperion-project.org/en/json/Authorization.html#request-a-token
    # ============================================================================

    async def async_send_request_token(self, *_: Any, **kwargs: Any) -> bool:
        """Request an authorization token.

        The user will accept/deny the token request on the Web UI.
        """
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_REQUEST_TOKEN,
            },
            soft={const.KEY_ID: generate_random_auth_id()},
        )
        return await self._async_send_json(data)

    # This call uses a custom (longer) timeout by default, as the user needs to interact
    # with the Hyperion UI before it will return.
    async_request_token = AwaitResponseWrapper(
        async_send_request_token, timeout_secs=const.DEFAULT_REQUEST_TOKEN_TIMEOUT_SECS
    )

    async def async_send_request_token_abort(self, *_: Any, **kwargs: Any) -> bool:
        """Abort a request for an authorization token."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_AUTHORIZE,
                const.KEY_SUBCOMMAND: const.KEY_REQUEST_TOKEN,
                const.KEY_ACCEPT: False,
            },
        )
        return await self._async_send_json(data)

    async_request_token_abort = AwaitResponseWrapper(async_send_request_token_abort)

    # ====================
    # || Data API calls ||
    # ====================

    # ================
    # ** Adjustment **
    # ================

    @property
    def adjustment(self) -> Optional[List[Dict[str, Any]]]:
        """Return adjustment."""
        return self._get_serverinfo_value(const.KEY_ADJUSTMENT)

    def _update_adjustment(self, adjustment: Optional[List[Dict[str, Any]]]) -> None:
        """Update adjustment."""
        if (
            self._serverinfo is None
            or not adjustment
            or not isinstance(adjustment, list)
        ):
            return
        self._serverinfo[const.KEY_ADJUSTMENT] = adjustment

    async def async_send_set_adjustment(self, *_: Any, **kwargs: Any) -> bool:
        """Request that a color be set."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_ADJUSTMENT}
        )
        return await self._async_send_json(data)

    async_set_adjustment = AwaitResponseWrapper(async_send_set_adjustment)

    # =====================================================================
    # ** Clear **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#clear
    # =====================================================================

    async def async_send_clear(self, *_: Any, **kwargs: Any) -> bool:
        """Request that a priority be cleared."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_CLEAR}
        )
        return await self._async_send_json(data)

    async_clear = AwaitResponseWrapper(async_send_clear)

    # =====================================================================
    # ** Color **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#set-color
    # =====================================================================

    async def async_send_set_color(self, *_: Any, **kwargs: Any) -> bool:
        """Request that a color be set."""
        data = HyperionClient._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_COLOR},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    async_set_color = AwaitResponseWrapper(async_send_set_color)

    # ==================================================================================
    # ** Component **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#components
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#component-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#control-components
    # ==================================================================================

    @property
    def components(self) -> Optional[List[Dict[str, Any]]]:
        """Return components."""
        return self._get_serverinfo_value(const.KEY_COMPONENTS)

    def _update_component(self, new_component: Dict[str, Any]) -> None:
        """Update full Hyperion state."""
        if (
            self._serverinfo is None
            or not isinstance(new_component, dict)
            or const.KEY_NAME not in new_component
        ):
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

    async def async_send_set_component(self, *_: Any, **kwargs: Any) -> bool:
        """Request that a color be set."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_COMPONENTSTATE}
        )
        return await self._async_send_json(data)

    async_set_component = AwaitResponseWrapper(async_send_set_component)

    def is_on(
        self,
        components: Optional[List[str]] = None,
    ) -> bool:
        """Determine if components are on."""
        if components is None:
            components = [const.KEY_COMPONENTID_ALL, const.KEY_COMPONENTID_LEDDEVICE]
        elif not components:
            return False

        components_to_state = {}
        for component in self.components or []:
            name = component.get(const.KEY_NAME)
            state = component.get(const.KEY_ENABLED)
            if name is None or state is None:
                continue
            components_to_state[name] = state

        for component_target in components:
            if (
                component_target not in components_to_state
                or not components_to_state[component_target]
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
    def effects(self) -> Optional[List[Dict[str, Any]]]:
        """Return effects."""
        return self._get_serverinfo_value(const.KEY_EFFECTS)

    def _update_effects(self, effects: List[Dict[str, Any]]) -> None:
        """Update effects."""
        if self._serverinfo is None or not isinstance(effects, list):
            return
        self._serverinfo[const.KEY_EFFECTS] = effects

    async def async_send_set_effect(self, *_: Any, **kwargs: Any) -> bool:
        """Request that an effect be set."""
        data = HyperionClient._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_EFFECT},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    async_set_effect = AwaitResponseWrapper(async_send_set_effect)

    # =================================================================================
    # ** Image **
    # Set: https://docs.hyperion-project.org/en/json/Control.html#set-image
    # =================================================================================

    async def async_send_set_image(self, *_: Any, **kwargs: Any) -> bool:
        """Request that an image be set."""
        data = HyperionClient._set_data(
            kwargs,
            hard={const.KEY_COMMAND: const.KEY_IMAGE},
            soft={const.KEY_ORIGIN: self._origin},
        )
        return await self._async_send_json(data)

    async_set_image = AwaitResponseWrapper(async_send_set_image)

    # ================================================================================
    # ** Image Streaming **
    # Update: https://docs.hyperion-project.org/en/json/Control.html#live-image-stream
    # Set: https://docs.hyperion-project.org/en/json/Control.html#live-image-stream
    # ================================================================================

    async def async_send_image_stream_start(self, *_: Any, **kwargs: Any) -> bool:
        """Request a live image stream to start."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_IMAGE_STREAM_START,
            },
        )
        return await self._async_send_json(data)

    async_image_stream_start = AwaitResponseWrapper(async_send_image_stream_start)

    async def async_send_image_stream_stop(self, *_: Any, **kwargs: Any) -> bool:
        """Request a live image stream to stop."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_IMAGE_STREAM_STOP,
            },
        )
        return await self._async_send_json(data)

    async_image_stream_stop = AwaitResponseWrapper(async_send_image_stream_stop)

    # =================================================================================
    # ** Instances **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#instance
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#instance-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#control-instances
    # =================================================================================

    @property
    def instances(self) -> Optional[List[Dict[str, Any]]]:
        """Return instances."""
        return self._get_serverinfo_value(const.KEY_INSTANCE)

    def _update_instances(self, instances: List[Dict[str, Any]]) -> None:
        """Update instances."""
        if self._serverinfo is None or not isinstance(instances, list):
            return
        self._serverinfo[const.KEY_INSTANCE] = instances

    async def async_send_start_instance(self, *_: Any, **kwargs: Any) -> bool:
        """Start an instance."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_START_INSTANCE,
            },
        )
        return await self._async_send_json(data)

    async_start_instance = AwaitResponseWrapper(async_send_start_instance)

    async def async_send_stop_instance(self, *_: Any, **kwargs: Any) -> bool:
        """Stop an instance."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_STOP_INSTANCE,
            },
        )
        return await self._async_send_json(data)

    async_stop_instance = AwaitResponseWrapper(async_send_stop_instance)

    async def async_send_switch_instance(self, *_: Any, **kwargs: Any) -> bool:
        """Stop an instance."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_INSTANCE,
                const.KEY_SUBCOMMAND: const.KEY_SWITCH_TO,
            },
        )
        return await self._async_send_json(data)

    async_switch_instance = AwaitResponseWrapper(async_send_switch_instance)

    # =============================================================================
    # ** LEDs **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#leds
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#leds-updates
    # =============================================================================

    @property
    def leds(self) -> Optional[List[Dict[str, Any]]]:
        """Return LEDs."""
        return self._get_serverinfo_value(const.KEY_LEDS)

    def _update_leds(self, leds: List[Dict[str, Any]]) -> None:
        """Update LEDs."""
        if self._serverinfo is None or not isinstance(leds, list):
            return
        self._serverinfo[const.KEY_LEDS] = leds

    # pylint: disable=line-too-long
    # =================================================================================
    # ** LED Mapping **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#led-mapping
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#led-mapping-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#led-mapping
    # =================================================================================

    @property
    def led_mapping_type(self) -> Optional[str]:
        """Return LED mapping type."""
        return self._get_serverinfo_value(const.KEY_LED_MAPPING_TYPE)

    def _update_led_mapping_type(self, led_mapping_type: str) -> None:
        """Update LED mapping  type."""
        if self._serverinfo is None or not isinstance(led_mapping_type, str):
            return
        self._serverinfo[const.KEY_LED_MAPPING_TYPE] = led_mapping_type

    async def async_send_set_led_mapping_type(self, *_: Any, **kwargs: Any) -> bool:
        """Request the LED mapping type be set."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_PROCESSING}
        )
        return await self._async_send_json(data)

    async_set_led_mapping_type = AwaitResponseWrapper(async_send_set_led_mapping_type)

    # =================================================================================
    # ** Live LED Streaming **
    # Update: https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream
    # Set: https://docs.hyperion-project.org/en/json/Control.html#live-led-color-stream
    # =================================================================================

    async def async_send_led_stream_start(self, *_: Any, **kwargs: Any) -> bool:
        """Request a live led stream to start."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_LED_STREAM_START,
            },
        )
        return await self._async_send_json(data)

    async_led_stream_start = AwaitResponseWrapper(async_send_led_stream_start)

    async def async_send_led_stream_stop(self, *_: Any, **kwargs: Any) -> bool:
        """Request a live led stream to stop."""
        data = HyperionClient._set_data(
            kwargs,
            hard={
                const.KEY_COMMAND: const.KEY_LEDCOLORS,
                const.KEY_SUBCOMMAND: const.KEY_LED_STREAM_STOP,
            },
        )
        return await self._async_send_json(data)

    async_led_stream_stop = AwaitResponseWrapper(async_send_led_stream_stop)

    # =================================================================================
    # ** Priorites **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#priorities
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#priority-updates
    # =================================================================================

    @property
    def priorities(self) -> Optional[List[Dict[str, Any]]]:
        """Return priorites."""
        return self._get_serverinfo_value(const.KEY_PRIORITIES)

    def _update_priorities(self, priorities: List[Dict[str, Any]]) -> None:
        """Update priorites."""
        if self._serverinfo is None or not isinstance(priorities, list):
            return
        self._serverinfo[const.KEY_PRIORITIES] = priorities

    @property
    def visible_priority(self) -> Optional[Dict[str, Any]]:
        """Return the visible priority, if any."""
        # The visible priority is supposed to be the first returned by the
        # API, but due to a bug the ordering is incorrect search for it
        # instead, see:
        # https://github.com/hyperion-project/hyperion.ng/issues/964
        for priority in self.priorities or []:
            if priority.get(const.KEY_VISIBLE, False):
                return priority
        return None

    # =================================================================================
    # ** Priorites Autoselect **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#priorities-selection-auto-manual
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#priority-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#source-selection
    # =================================================================================

    @property
    def priorities_autoselect(self) -> Optional[bool]:
        """Return priorites."""
        return self._get_serverinfo_value(const.KEY_PRIORITIES_AUTOSELECT)

    def _update_priorities_autoselect(self, priorities_autoselect: bool) -> None:
        """Update priorites."""
        if self._serverinfo is None or not isinstance(priorities_autoselect, bool):
            return
        self._serverinfo[const.KEY_PRIORITIES_AUTOSELECT] = priorities_autoselect

    async def async_send_set_sourceselect(self, *_: Any, **kwargs: Any) -> bool:
        """Request the sourceselect be set."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_SOURCESELECT}
        )
        return await self._async_send_json(data)

    async_set_sourceselect = AwaitResponseWrapper(async_send_set_sourceselect)

    # ================================================================================
    # ** Sessions **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#sessions
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#session-updates
    # ================================================================================

    @property
    def sessions(self) -> Optional[List[Dict[str, Any]]]:
        """Return sessions."""
        return self._get_serverinfo_value(const.KEY_SESSIONS)

    def _update_sessions(self, sessions: List[Dict[str, Any]]) -> None:
        """Update sessions."""
        if self._serverinfo is None or not isinstance(sessions, list):
            return
        self._serverinfo[const.KEY_SESSIONS] = sessions

    # =====================================================================
    # ** Serverinfo (full state) **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html
    # =====================================================================

    @property
    def serverinfo(self) -> Optional[Dict[str, Any]]:
        """Return current serverinfo."""
        return self._serverinfo

    def _update_serverinfo(self, state: Optional[Dict[str, Any]]) -> None:
        """Update full Hyperion state."""
        self._serverinfo = state

    def _get_serverinfo_value(self, key: str) -> Optional[Any]:
        """Get a value from serverinfo structure given key."""
        if not self._serverinfo:
            return None
        return self._serverinfo.get(key)

    async def async_send_get_serverinfo(self, *_: Any, **kwargs: Any) -> bool:
        """Server a serverinfo full state/subscription request."""
        # Request full state ('serverinfo') and subscribe to relevant
        # future updates to keep this object state accurate without the need to
        # poll.
        data = HyperionClient._set_data(
            kwargs,
            hard={
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
            },
        )
        return await self._async_send_json(data)

    async_get_serverinfo = AwaitResponseWrapper(async_send_get_serverinfo)

    # ==================================================================================
    # ** Videomode **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#video-mode
    # Update: https://docs.hyperion-project.org/en/json/Subscribe.html#videomode-updates
    # Set: https://docs.hyperion-project.org/en/json/Control.html#video-mode
    # ==================================================================================

    @property
    def videomode(self) -> Optional[str]:
        """Return videomode."""
        return self._get_serverinfo_value(const.KEY_VIDEOMODE)

    def _update_videomode(self, videomode: str) -> None:
        """Update videomode."""
        if self._serverinfo:
            self._serverinfo[const.KEY_VIDEOMODE] = videomode

    async def async_send_set_videomode(self, *_: Any, **kwargs: Any) -> bool:
        """Request the LED mapping type be set."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_VIDEOMODE}
        )
        return await self._async_send_json(data)

    async_set_videomode = AwaitResponseWrapper(async_send_set_videomode)

    # ==================================================================================
    # ** Sysinfo **
    # Full State: https://docs.hyperion-project.org/en/json/ServerInfo.html#system-hyperion
    # Returns system information from the Hyperion instance.
    # ==================================================================================

    async def async_send_sysinfo(self, *_: Any, **kwargs: Any) -> bool:
        """Request the sysinfo."""
        data = HyperionClient._set_data(
            kwargs, hard={const.KEY_COMMAND: const.KEY_SYSINFO}
        )
        return await self._async_send_json(data)

    async_sysinfo = AwaitResponseWrapper(async_send_sysinfo)

    async def async_sysinfo_id(self) -> Optional[str]:
        """Return an ID representing this Hyperion server."""
        sysinfo = await self.async_sysinfo()
        if sysinfo is not None and ResponseOK(sysinfo):
            sysinfo_id = (
                sysinfo.get(const.KEY_INFO, {})
                .get(const.KEY_HYPERION, {})
                .get(const.KEY_ID, None)
            )
            if not sysinfo_id or not isinstance(sysinfo_id, str):
                return None
            return str(sysinfo_id)
        return None

    async def async_sysinfo_version(self) -> Optional[str]:
        """Return the Hyperion server version."""
        sysinfo = await self.async_sysinfo()
        if sysinfo is not None and ResponseOK(sysinfo):
            sysinfo_version = (
                sysinfo.get(const.KEY_INFO, {})
                .get(const.KEY_HYPERION, {})
                .get(const.KEY_VERSION, None)
            )
            if not sysinfo_version or not isinstance(sysinfo_version, str):
                return None
            return str(sysinfo_version)
        return None


class ThreadedHyperionClient(threading.Thread):
    """Hyperion Client that runs in a dedicated thread."""

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        host: str,
        port: int = const.DEFAULT_PORT_JSON,
        default_callback: Optional[
            Union[HyperionCallback, Iterable[HyperionCallback]]
        ] = None,
        callbacks: Optional[
            Dict[str, Union[HyperionCallback, Iterable[HyperionCallback]]]
        ] = None,
        token: Optional[str] = None,
        instance: int = const.DEFAULT_INSTANCE,
        origin: str = const.DEFAULT_ORIGIN,
        timeout_secs: float = const.DEFAULT_TIMEOUT_SECS,
        retry_secs: int = const.DEFAULT_CONNECTION_RETRY_DELAY_SECS,
        raw_connection: bool = False,
    ) -> None:
        """Initialize client."""
        super().__init__()
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._hyperion_client: Optional[HyperionClient] = None

        self._client_init_call: Callable[[], HyperionClient] = lambda: HyperionClient(
            host,
            port,
            default_callback=default_callback,
            callbacks=callbacks,
            token=token,
            instance=instance,
            origin=origin,
            timeout_secs=timeout_secs,
            retry_secs=retry_secs,
            raw_connection=raw_connection,
        )
        self._client_init_event = threading.Event()

    def wait_for_client_init(self) -> None:
        """Block until the HyperionClient is ready to interact."""
        self._client_init_event.wait()

    async def _async_init_client(self) -> None:
        """Initialize the client."""
        # Initialize the client in the new thread, using the new event loop.
        # Some asyncio elements of the client (e.g. Conditions / Events) bind
        # to asyncio.get_event_loop() on construction.

        self._hyperion_client = self._client_init_call()

        for name, value in inspect.getmembers(
            self._hyperion_client, inspect.iscoroutinefunction
        ):
            if name.startswith("async_"):
                new_name = name[len("async_") :]
                self._register_async_call(new_name, value)
        for name, value in inspect.getmembers(
            type(self._hyperion_client), lambda o: isinstance(o, property)
        ):
            self._copy_property(name)

    def _copy_property(self, name: str) -> None:
        """Register a property."""
        setattr(
            type(self), name, property(lambda _: getattr(self._hyperion_client, name))
        )

    def _register_async_call(
        self, name: str, value: Callable[..., Awaitable[Any]]
    ) -> None:
        """Register a wrapped async call."""
        setattr(
            self,
            name,
            lambda *args, **kwargs: self._async_wrapper(value, *args, **kwargs),
        )

    def _async_wrapper(
        self, coro: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any
    ) -> Any:
        """Convert a async call to synchronous by running it in the local event loop."""
        future = asyncio.run_coroutine_threadsafe(coro(*args, **kwargs), self._loop)
        return future.result()

    def __getattr__(self, name: str) -> Any:
        """Override getattr to allow generous mypy treatment for dynamic methods."""
        return getattr(self, name)

    def stop(self) -> None:
        """Stop the asyncio loop and thus the thread."""

        def inner_stop() -> None:
            asyncio.get_event_loop().stop()

        self._loop.call_soon_threadsafe(inner_stop)

    def run(self) -> None:
        """Run the asyncio loop until stop is called."""
        asyncio.set_event_loop(self._loop)
        asyncio.get_event_loop().run_until_complete(self._async_init_client())
        self._client_init_event.set()
        asyncio.get_event_loop().run_forever()
        asyncio.get_event_loop().close()


class ResponseOK:
    """Small wrapper class around a server response."""

    def __init__(
        self,
        response: Optional[Dict[str, Any]],
        cmd: Optional[str] = None,
        validators: Optional[List[Callable[[Dict[str, Any]], bool]]] = None,
    ):
        """Initialize a Response object."""
        self._response = response
        self._cmd = cmd
        self._validators = validators or []

    def __bool__(self) -> bool:
        """Determine if the response indicates success."""
        if not self._response:
            return False
        if not isinstance(self._response, dict):
            return False
        if not self._response.get(const.KEY_SUCCESS, False):
            return False
        if self._cmd is not None and self._response.get(const.KEY_COMMAND) != self._cmd:
            return False
        for validator in self._validators:
            if not validator(self._response):
                return False
        return True


class ServerInfoResponseOK(ResponseOK):
    """Wrapper class for ServerInfo responses."""

    def __init__(self, response: Optional[Dict[str, Any]]):
        """Initialize the wrapper class."""
        super().__init__(
            response,
            cmd=const.KEY_SERVERINFO,
            validators=[lambda r: bool(r.get(const.KEY_INFO))],
        )


class LoginResponseOK(ResponseOK):
    """Wrapper class for LoginResponse."""

    def __init__(self, response: Optional[Dict[str, Any]]):
        """Initialize the wrapper class."""
        super().__init__(response, cmd=const.KEY_AUTHORIZE_LOGIN)


class SwitchInstanceResponseOK(ResponseOK):
    """Wrapper class for SwitchInstanceResponse."""

    def __init__(self, response: Optional[Dict[str, Any]]):
        """Initialize the wrapper class."""
        super().__init__(
            response,
            cmd=f"{const.KEY_INSTANCE}-{const.KEY_SWITCH_TO}",
            validators=[
                lambda r: r.get(const.KEY_INFO, {}).get(const.KEY_INSTANCE) is not None
            ],
        )


def generate_random_auth_id() -> str:
    """Generate random authenticate ID."""
    return "".join(
        random.choice(string.ascii_letters + string.digits) for i in range(0, 5)
    )
