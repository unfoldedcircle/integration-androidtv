"""
Pure connection lifecycle state machine for the Android TV integration driver.

Implements the sans-I/O finite state machine defined in
``docs/specs/001-connection-lifecycle-state-machine.md``. The FSM is a pure
``(state, trigger) -> (next_state, [intents])`` transformation: it performs no
network, timer, task, or event-emitter I/O of its own. All side effects are
expressed as :class:`Intent` values that a thin async executor performs.

This module intentionally imports only ``enum`` and ``logging`` (spec AC-10).

:copyright: (c) 2026 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from enum import IntEnum

_LOG = logging.getLogger(__name__)

# Grace window (seconds) between a transport loss and surfacing DISCONNECTED to the UI (INV-5).
# The FSM itself is timer-free: the executor arms a timer for this duration on the
# START_GRACE_TIMER intent and dispatches Trigger.GRACE_ELAPSED when it fires.
# NOTE: this value is provisional and requires field testing and adjustment (spec Decision 4).
# It must stay comfortably below the client's ~2-3 s request timeout to the integration and
# above the androidtvremote2 library's 0.1 s initial reconnect backoff.
RECONNECT_GRACE: float = 1.5


class ConnectionState(IntEnum):
    """Runtime connection states of an Android TV device."""

    DISCONNECTED = 0
    """Idle: no connection, not trying (initial, or after explicit disconnect/standby)."""
    CONNECTING = 1
    """Initial connect in progress (bounded retry loop)."""
    CONNECTED = 2
    """Transport live and available."""
    RECONNECTING = 3
    """Was connected, transport lost; library keep_reconnecting() is retrying."""
    AUTH_ERROR = 4
    """Needs re-pairing; terminal until reconfigured (INV-6)."""
    UNREACHABLE = 5
    """Initial connect gave up (max_timeout/fatal); terminal until user retry (INV-6)."""


class Trigger(IntEnum):
    """The only inputs to the FSM."""

    CONNECT_REQUESTED = 0
    """connect() called (user/driver/standby-exit/subscribe)."""
    CONNECT_SUCCEEDED = 1
    """Initial async_connect() returned."""
    CONNECT_FAILED_AUTH = 2
    """InvalidAuth during initial connect."""
    CONNECT_ABORTED = 3
    """max_timeout reached or fatal error during initial connect."""
    TRANSPORT_LOST = 4
    """is_available(False) library callback."""
    TRANSPORT_AVAILABLE = 5
    """is_available(True) library callback."""
    RECONNECT_AUTH_FAILED = 6
    """keep_reconnecting() invalid-auth callback."""
    GRACE_ELAPSED = 7
    """RECONNECTING grace timer fired without recovery."""
    DISCONNECT_REQUESTED = 8
    """disconnect() called (user/standby/removal)."""


class Intent(IntEnum):
    """Side effects the executor performs — the FSM itself performs none."""

    START_INITIAL_CONNECT = 0
    """Spawn the bounded initial-connect coroutine."""
    START_KEEP_RECONNECTING = 1
    """self._atv.keep_reconnecting(invalid_auth_cb)."""
    START_CAST = 2
    """await self._chromecast_connect()."""
    START_IP_WATCHER = 3
    """Spawn _rediscover_ip_while_disconnected() (Stage-1 helper)."""
    START_GRACE_TIMER = 4
    """Spawn grace timer -> dispatch GRACE_ELAPSED."""
    CANCEL_IP_WATCHER = 5
    """Cancel the IP-rediscovery watcher task."""
    CANCEL_GRACE_TIMER = 6
    """Cancel the grace timer task."""
    CANCEL_TASKS = 7
    """Cancel all tracked tasks (INV-7)."""
    ATV_DISCONNECT = 8
    """self._atv.disconnect()."""
    EMIT_CONNECTED = 9
    """events.emit(Events.CONNECTED, id)."""
    EMIT_DISCONNECTED = 10
    """events.emit(Events.DISCONNECTED, id)."""
    EMIT_AUTH_ERROR = 11
    """events.emit(Events.AUTH_ERROR, id)."""
    EMIT_RECONNECTING = 12
    """events.emit(Events.RECONNECTING, id) — new event, see spec Decision 3."""


# Transition table: single source of truth (spec 001 "Transition table").
# Any (state, trigger) pair not listed is a no-op: the FSM stays in the current state,
# returns [], and logs the ignored transition at DEBUG.
_TRANSITIONS: dict[tuple[ConnectionState, Trigger], tuple[ConnectionState, tuple[Intent, ...]]] = {
    (ConnectionState.DISCONNECTED, Trigger.CONNECT_REQUESTED): (
        ConnectionState.CONNECTING,
        (Intent.START_INITIAL_CONNECT,),
    ),
    (ConnectionState.CONNECTING, Trigger.CONNECT_SUCCEEDED): (
        ConnectionState.CONNECTED,
        (Intent.START_KEEP_RECONNECTING, Intent.START_CAST, Intent.EMIT_CONNECTED),
    ),
    (ConnectionState.CONNECTING, Trigger.CONNECT_FAILED_AUTH): (
        ConnectionState.AUTH_ERROR,
        (Intent.EMIT_AUTH_ERROR,),
    ),
    (ConnectionState.CONNECTING, Trigger.CONNECT_ABORTED): (
        ConnectionState.UNREACHABLE,
        (Intent.EMIT_DISCONNECTED,),
    ),
    (ConnectionState.CONNECTING, Trigger.DISCONNECT_REQUESTED): (
        ConnectionState.DISCONNECTED,
        (Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED),
    ),
    (ConnectionState.CONNECTED, Trigger.TRANSPORT_LOST): (
        ConnectionState.RECONNECTING,
        (Intent.START_IP_WATCHER, Intent.START_GRACE_TIMER, Intent.EMIT_RECONNECTING),
    ),
    (ConnectionState.CONNECTED, Trigger.DISCONNECT_REQUESTED): (
        ConnectionState.DISCONNECTED,
        (Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED),
    ),
    (ConnectionState.RECONNECTING, Trigger.TRANSPORT_AVAILABLE): (
        ConnectionState.CONNECTED,
        (Intent.CANCEL_IP_WATCHER, Intent.CANCEL_GRACE_TIMER, Intent.START_CAST, Intent.EMIT_CONNECTED),
    ),
    (ConnectionState.RECONNECTING, Trigger.GRACE_ELAPSED): (
        ConnectionState.RECONNECTING,
        (Intent.EMIT_DISCONNECTED,),
    ),
    (ConnectionState.RECONNECTING, Trigger.RECONNECT_AUTH_FAILED): (
        ConnectionState.AUTH_ERROR,
        (Intent.CANCEL_IP_WATCHER, Intent.CANCEL_GRACE_TIMER, Intent.EMIT_AUTH_ERROR),
    ),
    (ConnectionState.RECONNECTING, Trigger.DISCONNECT_REQUESTED): (
        ConnectionState.DISCONNECTED,
        (Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED),
    ),
    (ConnectionState.AUTH_ERROR, Trigger.CONNECT_REQUESTED): (
        ConnectionState.CONNECTING,
        (Intent.START_INITIAL_CONNECT,),
    ),
    (ConnectionState.AUTH_ERROR, Trigger.DISCONNECT_REQUESTED): (
        ConnectionState.DISCONNECTED,
        (Intent.CANCEL_TASKS, Intent.EMIT_DISCONNECTED),
    ),
    (ConnectionState.UNREACHABLE, Trigger.CONNECT_REQUESTED): (
        ConnectionState.CONNECTING,
        (Intent.START_INITIAL_CONNECT,),
    ),
    (ConnectionState.UNREACHABLE, Trigger.DISCONNECT_REQUESTED): (
        ConnectionState.DISCONNECTED,
        (Intent.CANCEL_TASKS, Intent.EMIT_DISCONNECTED),
    ),
}


class ConnectionFsm:
    """Pure, sans-I/O connection lifecycle state machine (single writer, INV-1).

    Feed it a :class:`Trigger` via :meth:`apply` and execute the returned
    :class:`Intent` list. The FSM mutates only its own in-memory state.
    """

    def __init__(self, state: ConnectionState = ConnectionState.DISCONNECTED) -> None:
        """Create a state machine, starting in the given state."""
        self._state: ConnectionState = state

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    def apply(self, trigger: Trigger) -> list[Intent]:
        """Apply a trigger. Pure except for updating the in-memory state.

        Returns the ordered list of intents the caller must execute. Unlisted
        (state, trigger) pairs are no-ops: state unchanged, returns [], logged at DEBUG.

        :param trigger: the trigger to apply to the current state.
        :return: ordered intents the caller must execute.
        """
        transition = _TRANSITIONS.get((self._state, trigger))
        if transition is None:
            _LOG.debug("Ignored transition: %s + %s (no-op)", self._state.name, trigger.name)
            return []
        next_state, intents = transition
        _LOG.debug("Transition: %s + %s -> %s %s", self._state.name, trigger.name, next_state.name, list(intents))
        self._state = next_state
        return list(intents)
