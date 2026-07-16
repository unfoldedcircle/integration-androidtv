"""
Integration tests for the AndroidTv connection lifecycle FSM executor (spec 001, Phase 2).

Drives ``AndroidTv`` with a fake ``AndroidTVRemote`` per the spec's Test Plan section
"tests/test_tv_connection.py". No network, no real sleeps: grace-timer behaviour is
tested by dispatching ``GRACE_ELAPSED`` directly, and the bounded initial-connect
timeout is tested with a fake clock.

:copyright: (c) 2026 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import ast
import asyncio
import sys
import unittest
from functools import partial
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    # tv.py uses top-level imports (apps, config, ...), so import it the same way the driver does
    sys.path.insert(0, str(_SRC_DIR))

# pylint: disable=wrong-import-position
import ucapi  # noqa: E402
from androidtvremote2 import CannotConnect, InvalidAuth  # noqa: E402

import tv  # noqa: E402
from config import AtvDevice  # noqa: E402
from connection_fsm import ConnectionState, Trigger  # noqa: E402
from tv import AndroidTv, Events  # noqa: E402


class _FakeTransport:
    """Transport stub with a controllable is_closing() result."""

    def __init__(self) -> None:
        self.closing: bool = False

    def is_closing(self) -> bool:
        return self.closing


class _FakeRemoteProtocol:
    """Remote message protocol stub exposing a fake transport."""

    def __init__(self) -> None:
        self.transport = _FakeTransport()


class FakeAndroidTVRemote:
    """Fake androidtvremote2.AndroidTVRemote — no network, fully controllable."""

    # pylint: disable=too-many-instance-attributes,unused-argument
    def __init__(
        self,
        client_name: str = "",
        certfile: str = "",
        keyfile: str = "",
        host: str = "",
        loop: asyncio.AbstractEventLoop | None = None,
        enable_voice: bool = True,
    ) -> None:
        self.host = host
        self.is_on: bool | None = None
        self.current_app: str | None = None
        self.device_info: dict[str, str] = {"manufacturer": "Fake", "model": "TV"}
        self.is_voice_enabled: bool | None = None
        self._loop = loop or asyncio.get_event_loop()

        self.connect_error: Exception | None = None
        """Exception raised by async_connect() if set (InvalidAuth / CannotConnect)."""
        self.connect_gate: asyncio.Event | None = None
        """If set, async_connect() blocks until the event is set (to test disconnect while connecting)."""

        self.async_connect_calls: int = 0
        self.disconnect_calls: int = 0
        self.keep_reconnecting_calls: int = 0
        self.invalid_auth_callback: Callable[[], None] | None = None
        self.is_on_updated_cb: Callable[[bool], None] | None = None
        self.current_app_updated_cb: Callable[[str], None] | None = None
        self.volume_info_updated_cb: Callable[[dict], None] | None = None
        self.is_available_updated_cb: Callable[[bool], None] | None = None

        self._remote_message_protocol: _FakeRemoteProtocol | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_result: asyncio.Future | None = None

    # callback registrars

    def add_is_on_updated_callback(self, callback: Callable[[bool], None]) -> None:
        self.is_on_updated_cb = callback

    def add_current_app_updated_callback(self, callback: Callable[[str], None]) -> None:
        self.current_app_updated_cb = callback

    def add_volume_info_updated_callback(self, callback: Callable[[dict], None]) -> None:
        self.volume_info_updated_cb = callback

    def add_is_available_updated_callback(self, callback: Callable[[bool], None]) -> None:
        self.is_available_updated_cb = callback

    # connection API

    async def async_connect(self) -> None:
        self.async_connect_calls += 1
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        if self.connect_error is not None:
            raise self.connect_error
        self._remote_message_protocol = _FakeRemoteProtocol()
        self.is_on = True

    def keep_reconnecting(self, invalid_auth_callback: Callable[[], None] | None = None) -> None:
        self.keep_reconnecting_calls += 1
        self.invalid_auth_callback = invalid_auth_callback
        self._reconnect_result = self._loop.create_future()
        self._reconnect_task = self._loop.create_task(self._reconnect_owner())

    async def _reconnect_owner(self) -> None:
        assert self._reconnect_result is not None
        await self._reconnect_result

    def fail_reconnect_owner(self, exception: Exception) -> None:
        """Terminate the reconnect owner task with an exception (spec F11)."""
        assert self._reconnect_result is not None
        self._reconnect_result.set_exception(exception)

    def finish_reconnect_owner(self) -> None:
        """Terminate the reconnect owner task without exception (library exit on invalid auth)."""
        assert self._reconnect_result is not None
        self._reconnect_result.set_result(None)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        # mirrors androidtvremote2 0.3.1: disconnect() cancels the reconnect task and drops the protocol
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
        self._remote_message_protocol = None

    # test helpers

    def make_transport_live(self) -> None:
        self._remote_message_protocol = _FakeRemoteProtocol()

    def drop_transport(self) -> None:
        self._remote_message_protocol = None


class _FakeTime:
    """time-module stand-in: every time() call advances the clock by 100 s (no real sleeps)."""

    def __init__(self) -> None:
        self.now: float = 0.0

    def time(self) -> float:
        self.now += 100.0
        return self.now


class TvConnectionTestCase(unittest.IsolatedAsyncioTestCase):
    """Base fixture: an AndroidTv wired to a FakeAndroidTVRemote, with event capture."""

    # pylint: disable=protected-access

    async def asyncSetUp(self) -> None:
        self.loop = asyncio.get_running_loop()
        for patcher in (
            patch.object(tv, "AndroidTVRemote", FakeAndroidTVRemote),
            # keep the (real) grace timer far in the future; grace tests dispatch GRACE_ELAPSED directly
            patch.object(tv, "RECONNECT_GRACE", 60.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        device = AtvDevice(id="fake1", name="Fake TV", address="10.0.0.2")
        self.atv = AndroidTv("cert.pem", "key.pem", device, profile=None, loop=self.loop)
        self.fake: FakeAndroidTVRemote = self.atv._atv  # type: ignore[assignment]
        self.events: dict[Events, int] = {event: 0 for event in Events}
        for event in Events:
            self.atv.events.on(event, partial(self._record_event, event))

    async def asyncTearDown(self) -> None:
        self.atv.events.remove_all_listeners()
        self.atv.disconnect()
        await self._drain()

    def _record_event(self, event: Events, *_args: Any) -> None:
        self.events[event] += 1

    async def _drain(self, rounds: int = 10) -> None:
        """Give scheduled callbacks and cancellations a chance to run (no real sleeps)."""
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def _wait_for(self, predicate: Callable[[], bool], timeout: float = 2.0) -> None:
        """Yield to the loop until the predicate holds; fail after the wall-clock timeout."""
        deadline = self.loop.time() + timeout
        while not predicate():
            if self.loop.time() > deadline:
                self.fail("condition not met in time")
            await asyncio.sleep(0)

    def _drop_transport_and_notify(self) -> None:
        """Simulate a transport loss: drop the protocol and fire the library is_available(False) callback."""
        self.fake.drop_transport()
        assert self.fake.is_available_updated_cb is not None
        self.fake.is_available_updated_cb(False)

    def _restore_transport_and_notify(self) -> None:
        """Simulate library reconnection: restore the protocol and fire is_available(True)."""
        self.fake.make_transport_live()
        assert self.fake.is_available_updated_cb is not None
        self.fake.is_available_updated_cb(True)


class InitialConnectTest(TvConnectionTestCase):
    """Test-Plan items 1 and 2 plus the initial-connect failure paths."""

    async def test_initial_success(self):
        """Item 1: keep_reconnecting once, CONNECTED emitted once, state CONNECTED (INV-2/INV-3)."""
        result = await self.atv.connect()

        self.assertTrue(result)
        self.assertEqual(self.fake.keep_reconnecting_calls, 1)
        self.assertEqual(self.events[Events.CONNECTED], 1)
        self.assertEqual(self.events[Events.DISCONNECTED], 0)
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)

    async def test_stale_is_on_with_closed_transport_reconnects(self):
        """Item 2 (P0-3): is_on=True with a closing transport must not short-circuit connect() (INV-4)."""
        self.fake.is_on = True
        self.fake.make_transport_live()
        assert self.fake._remote_message_protocol is not None
        self.fake._remote_message_protocol.transport.closing = True
        self.assertFalse(self.atv._has_live_connection())

        result = await self.atv.connect()

        self.assertTrue(result)
        self.assertEqual(self.fake.async_connect_calls, 1, "connect() must proceed instead of short-circuiting")
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)

    async def test_connect_while_connected_is_idempotent(self):
        """connect() with a live connection re-emits CONNECTED but starts nothing new."""
        await self.atv.connect()

        result = await self.atv.connect()

        self.assertTrue(result)
        self.assertEqual(self.fake.async_connect_calls, 1)
        self.assertEqual(self.fake.keep_reconnecting_calls, 1)
        self.assertEqual(self.events[Events.CONNECTED], 2)
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)

    async def test_initial_connect_invalid_auth(self):
        """InvalidAuth during initial connect: AUTH_ERROR state and event, no reconnect (INV-6)."""
        self.fake.connect_error = InvalidAuth("bad cert")

        result = await self.atv.connect()

        self.assertFalse(result)
        self.assertEqual(self.atv.connection_state, ConnectionState.AUTH_ERROR)
        self.assertEqual(self.events[Events.AUTH_ERROR], 1)
        self.assertEqual(self.fake.keep_reconnecting_calls, 0)

    async def test_initial_connect_aborted_after_max_timeout(self):
        """CannotConnect past max_timeout: UNREACHABLE state, exactly one DISCONNECTED (INV-6)."""
        self.fake.connect_error = CannotConnect("nope")

        with patch.object(tv, "time", _FakeTime()):  # fake clock: abort check trips without real sleeps
            result = await self.atv.connect(max_timeout=1)

        self.assertFalse(result)
        self.assertEqual(self.atv.connection_state, ConnectionState.UNREACHABLE)
        self.assertEqual(self.events[Events.DISCONNECTED], 1)
        self.assertEqual(self.fake.keep_reconnecting_calls, 0)


class CommandPathTest(TvConnectionTestCase):
    """Test-Plan item 3 (P0-2 regression)."""

    async def test_command_while_reconnecting_returns_service_unavailable(self):
        """A command while RECONNECTING must not spawn a connect nor cancel the library reconnect (INV-2)."""
        await self.atv.connect()
        connects_before = self.fake.async_connect_calls

        self._drop_transport_and_notify()
        self.assertEqual(self.atv.connection_state, ConnectionState.RECONNECTING)

        result = await self.atv._send_command("POWER")

        self.assertEqual(result, ucapi.StatusCodes.SERVICE_UNAVAILABLE)
        self.assertEqual(self.fake.async_connect_calls, connects_before, "no second connect may be spawned")
        assert self.fake._reconnect_task is not None
        self.assertFalse(self.fake._reconnect_task.done(), "library reconnect must keep running")
        self.assertEqual(self.fake._reconnect_task.cancelling(), 0, "library reconnect must not be cancelled")
        self.assertEqual(self.fake.keep_reconnecting_calls, 1)


class IpRediscoveryTest(TvConnectionTestCase):
    """Test-Plan item 4 (P1-1)."""

    async def test_changed_ip_is_discovered_while_reconnecting(self):
        """While RECONNECTING, a changed discovered IP updates _atv.host and emits IP_ADDRESS_CHANGED."""
        await self.atv.connect()

        async def _fake_discover() -> list[dict[str, str]]:
            # revive the transport so the watcher loop exits after applying the new address
            self.fake.make_transport_live()
            return [{"name": "Fake TV", "address": "10.0.0.99"}]

        with (
            patch.object(tv, "RECONNECT_DISCOVERY_DELAY", 0),
            patch.object(tv, "RECONNECT_DISCOVERY_INTERVAL", 0),
            patch.object(tv.discover, "android_tvs", _fake_discover),
        ):
            self._drop_transport_and_notify()
            watcher = self.atv._ip_rediscovery_task
            self.assertIsNotNone(watcher, "TRANSPORT_LOST must start the IP watcher")
            await asyncio.wait_for(watcher, 2)

        self.assertEqual(self.fake.host, "10.0.0.99")
        self.assertEqual(self.events[Events.IP_ADDRESS_CHANGED], 1)


class FlickerSuppressionTest(TvConnectionTestCase):
    """Test-Plan item 5 (INV-5), end-to-end. GRACE_ELAPSED is dispatched directly (no real sleeps)."""

    async def test_recovery_within_grace_emits_no_disconnected(self):
        await self.atv.connect()

        self._drop_transport_and_notify()
        self.assertEqual(self.atv.connection_state, ConnectionState.RECONNECTING)
        self.assertEqual(self.events[Events.RECONNECTING], 1)
        self.assertEqual(self.events[Events.DISCONNECTED], 0)

        self._restore_transport_and_notify()
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)
        self.assertEqual(self.events[Events.DISCONNECTED], 0, "no DISCONNECTED flicker within grace (INV-5)")
        self.assertEqual(self.events[Events.CONNECTED], 2)

    async def test_exceeding_grace_emits_exactly_one_disconnected(self):
        await self.atv.connect()

        self._drop_transport_and_notify()
        self.atv._dispatch(Trigger.GRACE_ELAPSED)

        self.assertEqual(self.atv.connection_state, ConnectionState.RECONNECTING)
        self.assertEqual(self.events[Events.DISCONNECTED], 1)

        # late recovery still emits CONNECTED and no further DISCONNECTED
        self._restore_transport_and_notify()
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)
        self.assertEqual(self.events[Events.DISCONNECTED], 1)


class TeardownTest(TvConnectionTestCase):
    """Test-Plan item 6 (INV-7): disconnect() cancels every owned task and lands in DISCONNECTED."""

    async def test_disconnect_while_connecting(self):
        self.fake.connect_gate = asyncio.Event()  # async_connect() hangs
        connect_task = self.loop.create_task(self.atv.connect())
        await self._wait_for(lambda: self.fake.async_connect_calls == 1)
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTING)
        tracked = list(self.atv._tasks)
        self.assertTrue(tracked)

        self.atv.disconnect()

        self.assertEqual(self.atv.connection_state, ConnectionState.DISCONNECTED)
        self.assertFalse(await connect_task, "aborted connect() must return False")
        await self._drain()
        self.assertFalse(self.atv._tasks, "no tracked task may survive disconnect()")
        for task in tracked:
            self.assertTrue(task.cancelled())
        self.assertEqual(self.events[Events.DISCONNECTED], 1)
        self.assertEqual(self.events[Events.CONNECTED], 0, "cancelled initial connect must not dispatch success")

    async def test_disconnect_while_connected(self):
        await self.atv.connect()

        self.atv.disconnect()

        self.assertEqual(self.atv.connection_state, ConnectionState.DISCONNECTED)
        self.assertGreaterEqual(self.fake.disconnect_calls, 1)
        await self._drain()
        assert self.fake._reconnect_task is not None
        self.assertTrue(self.fake._reconnect_task.cancelled(), "library reconnect owner must be stopped")
        self.assertFalse(self.atv._tasks)
        self.assertEqual(self.events[Events.DISCONNECTED], 1)

    async def test_disconnect_while_reconnecting(self):
        await self.atv.connect()
        self._drop_transport_and_notify()
        watcher = self.atv._ip_rediscovery_task
        grace = self.atv._grace_timer_task
        self.assertIsNotNone(watcher)
        self.assertIsNotNone(grace)

        self.atv.disconnect()

        self.assertEqual(self.atv.connection_state, ConnectionState.DISCONNECTED)
        await self._drain()
        self.assertTrue(watcher.cancelled())
        self.assertTrue(grace.cancelled())
        self.assertIsNone(self.atv._ip_rediscovery_task)
        self.assertIsNone(self.atv._grace_timer_task)
        self.assertFalse(self.atv._tasks)


class AuthTerminalTest(TvConnectionTestCase):
    """Test-Plan item 7 (INV-6)."""

    async def test_reconnect_auth_failure_is_terminal(self):
        await self.atv.connect()
        self._drop_transport_and_notify()
        watcher = self.atv._ip_rediscovery_task
        grace = self.atv._grace_timer_task

        assert self.fake.invalid_auth_callback is not None
        self.fake.invalid_auth_callback()
        # the library reconnect owner exits without exception after invalid auth
        self.fake.finish_reconnect_owner()
        await self._drain()

        self.assertEqual(self.atv.connection_state, ConnectionState.AUTH_ERROR)
        self.assertEqual(self.events[Events.AUTH_ERROR], 1)
        self.assertTrue(watcher.cancelled(), "IP watcher must be cancelled on AUTH_ERROR")
        self.assertTrue(grace.cancelled(), "grace timer must be cancelled on AUTH_ERROR")
        self.assertEqual(self.fake.keep_reconnecting_calls, 1, "no automatic reconnect from AUTH_ERROR")
        self.assertEqual(self.fake.async_connect_calls, 1)

        # terminal: transport events do not leave AUTH_ERROR
        self._restore_transport_and_notify()
        self.assertEqual(self.atv.connection_state, ConnectionState.AUTH_ERROR)


class SingleWriterAuditTest(unittest.TestCase):
    """Test-Plan item 8 (INV-1): runtime connection state is written only by ConnectionFsm.apply().

    Phase 1 named the FSM state attribute ``self._state`` (inside connection_fsm.py); the
    spec's literal grep target ``self._conn_state`` is asserted as well. In tv.py the same
    attribute name is legitimately used for the out-of-scope init/pairing DeviceState, so the
    audit verifies that every ``self._state`` write in tv.py assigns a DeviceState member from
    an allowed init/pairing method, and that the FSM instance itself is never written to.
    """

    _ALLOWED_DEVICE_STATE_WRITERS = {"__init__", "init", "start_pairing", "finish_pairing"}

    @staticmethod
    def _attribute_writes(tree: ast.Module) -> list[tuple[str, ast.Attribute, ast.AST]]:
        """Return (enclosing_function, target_attribute, value) for all self.* attribute assignments."""
        writes: list[tuple[str, ast.Attribute, ast.AST]] = []

        def visit(node: ast.AST, func: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = node.name
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        writes.append((func, target, node.value))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Attribute):
                writes.append((func, node.target, node.value))
            for child in ast.iter_child_nodes(node):
                visit(child, func)

        visit(tree, "<module>")
        return writes

    def test_no_conn_state_writes_outside_connection_fsm(self):
        """Spec AC-1 grep target: no `self._conn_state = ` assignment exists outside connection_fsm.py."""
        for path in sorted(_SRC_DIR.glob("*.py")):
            if path.name == "connection_fsm.py":
                continue
            self.assertNotIn("self._conn_state", path.read_text(encoding="utf-8"), f"found in {path.name}")

    def test_tv_state_writes_are_device_state_only_in_init_and_pairing(self):
        """tv.py must never shadow-write the runtime connection state."""
        tree = ast.parse((_SRC_DIR / "tv.py").read_text(encoding="utf-8"))
        state_writes = 0
        for func, target, value in self._attribute_writes(tree):
            if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                continue
            # the FSM instance may only be created in __init__, never reassigned elsewhere
            if target.attr == "_conn":
                self.assertEqual(func, "__init__", f"self._conn reassigned in {func}")
                self.assertTrue(
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "ConnectionFsm",
                    "self._conn must be initialized with a ConnectionFsm instance",
                )
                continue
            if isinstance(target.value, ast.Attribute):  # e.g. self._conn._state
                self.fail(f"nested attribute write on self in {func}")
            if target.attr != "_state":
                continue
            state_writes += 1
            self.assertIn(
                func, self._ALLOWED_DEVICE_STATE_WRITERS, f"runtime `self._state` write in disallowed method {func}"
            )
            self.assertTrue(
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "DeviceState",
                f"`self._state` in {func} must only be assigned DeviceState members",
            )
        self.assertGreater(state_writes, 0, "audit is broken: no self._state writes found at all")

    def test_tv_never_writes_fsm_internals(self):
        """No `self._conn.<attr> = ...` write exists in tv.py (apply() is the single writer)."""
        tree = ast.parse((_SRC_DIR / "tv.py").read_text(encoding="utf-8"))
        for func, target, _value in self._attribute_writes(tree):
            inner = target.value
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "_conn"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "self"
            ):
                self.fail(f"write to FSM internals `self._conn.{target.attr}` in {func}")


class ReconnectOwnerSupervisionTest(TvConnectionTestCase):
    """Test-Plan item 9 (spec F11)."""

    async def test_silently_dying_reconnect_owner_triggers_recovery(self):
        """Exceptional completion without auth callback: error logged, ownership re-established."""
        await self.atv.connect()
        self.assertEqual(self.fake.keep_reconnecting_calls, 1)

        with self.assertLogs("tv", level="ERROR") as logs:
            self.fake.fail_reconnect_owner(PermissionError("cert file unreadable"))
            # recovery: DISCONNECT_REQUESTED + CONNECT_REQUESTED -> fresh keep_reconnecting
            await self._wait_for(lambda: self.fake.keep_reconnecting_calls == 2)

        self.assertTrue(any("Reconnect owner terminated unexpectedly" in message for message in logs.output))
        self.assertEqual(self.fake.async_connect_calls, 2, "a fresh initial connect must have run")
        self.assertGreaterEqual(self.fake.disconnect_calls, 1, "recovery must go through DISCONNECT_REQUESTED")
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)

    async def test_cancelled_reconnect_owner_triggers_nothing(self):
        """A cancelled reconnect task is a deliberate stop: no recovery dispatches."""
        await self.atv.connect()
        assert self.fake._reconnect_task is not None

        self.fake._reconnect_task.cancel()
        await self._drain()

        self.assertEqual(self.fake.keep_reconnecting_calls, 1)
        self.assertEqual(self.fake.async_connect_calls, 1)
        self.assertEqual(self.atv.connection_state, ConnectionState.CONNECTED)
        self.assertEqual(self.events[Events.DISCONNECTED], 0)


if __name__ == "__main__":
    unittest.main()
