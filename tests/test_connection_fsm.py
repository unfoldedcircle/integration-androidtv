"""
Pure unit tests for the connection lifecycle state machine (spec 001, Phase 1).

Sans-I/O test suite per spec AC-8: covers every row of the transition table plus
representative no-op pairs, with no event loop, no clock, no sockets, and no fakes.
Grace-timer behaviour is tested by injecting the GRACE_ELAPSED trigger directly.

:copyright: (c) 2026 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import ast
import unittest
from pathlib import Path

from src.connection_fsm import ConnectionFsm, ConnectionState, Intent, Trigger


class TransitionTableTest(unittest.TestCase):
    """One test per listed row of the transition table asserts (next_state, intents)."""

    def _assert_transition(
        self,
        from_state: ConnectionState,
        trigger: Trigger,
        to_state: ConnectionState,
        intents: list[Intent],
    ) -> None:
        fsm = ConnectionFsm(from_state)
        result = fsm.apply(trigger)
        self.assertEqual(fsm.state, to_state)
        self.assertEqual(result, intents)

    def test_disconnected_connect_requested(self):
        self._assert_transition(
            ConnectionState.DISCONNECTED,
            Trigger.CONNECT_REQUESTED,
            ConnectionState.CONNECTING,
            [Intent.START_INITIAL_CONNECT],
        )

    def test_connecting_connect_succeeded(self):
        self._assert_transition(
            ConnectionState.CONNECTING,
            Trigger.CONNECT_SUCCEEDED,
            ConnectionState.CONNECTED,
            [Intent.START_KEEP_RECONNECTING, Intent.START_CAST, Intent.EMIT_CONNECTED],
        )

    def test_connecting_connect_failed_auth(self):
        self._assert_transition(
            ConnectionState.CONNECTING,
            Trigger.CONNECT_FAILED_AUTH,
            ConnectionState.AUTH_ERROR,
            [Intent.EMIT_AUTH_ERROR],
        )

    def test_connecting_connect_aborted(self):
        self._assert_transition(
            ConnectionState.CONNECTING,
            Trigger.CONNECT_ABORTED,
            ConnectionState.UNREACHABLE,
            [Intent.EMIT_DISCONNECTED],
        )

    def test_connecting_disconnect_requested(self):
        self._assert_transition(
            ConnectionState.CONNECTING,
            Trigger.DISCONNECT_REQUESTED,
            ConnectionState.DISCONNECTED,
            [Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED],
        )

    def test_connected_transport_lost(self):
        self._assert_transition(
            ConnectionState.CONNECTED,
            Trigger.TRANSPORT_LOST,
            ConnectionState.RECONNECTING,
            [Intent.START_IP_WATCHER, Intent.START_GRACE_TIMER, Intent.EMIT_RECONNECTING],
        )

    def test_connected_disconnect_requested(self):
        self._assert_transition(
            ConnectionState.CONNECTED,
            Trigger.DISCONNECT_REQUESTED,
            ConnectionState.DISCONNECTED,
            [Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED],
        )

    def test_reconnecting_transport_available(self):
        self._assert_transition(
            ConnectionState.RECONNECTING,
            Trigger.TRANSPORT_AVAILABLE,
            ConnectionState.CONNECTED,
            [Intent.CANCEL_IP_WATCHER, Intent.CANCEL_GRACE_TIMER, Intent.START_CAST, Intent.EMIT_CONNECTED],
        )

    def test_reconnecting_grace_elapsed(self):
        self._assert_transition(
            ConnectionState.RECONNECTING,
            Trigger.GRACE_ELAPSED,
            ConnectionState.RECONNECTING,
            [Intent.EMIT_DISCONNECTED],
        )

    def test_reconnecting_reconnect_auth_failed(self):
        self._assert_transition(
            ConnectionState.RECONNECTING,
            Trigger.RECONNECT_AUTH_FAILED,
            ConnectionState.AUTH_ERROR,
            [Intent.CANCEL_IP_WATCHER, Intent.CANCEL_GRACE_TIMER, Intent.EMIT_AUTH_ERROR],
        )

    def test_reconnecting_disconnect_requested(self):
        self._assert_transition(
            ConnectionState.RECONNECTING,
            Trigger.DISCONNECT_REQUESTED,
            ConnectionState.DISCONNECTED,
            [Intent.CANCEL_TASKS, Intent.ATV_DISCONNECT, Intent.EMIT_DISCONNECTED],
        )

    def test_auth_error_connect_requested(self):
        self._assert_transition(
            ConnectionState.AUTH_ERROR,
            Trigger.CONNECT_REQUESTED,
            ConnectionState.CONNECTING,
            [Intent.START_INITIAL_CONNECT],
        )

    def test_auth_error_disconnect_requested(self):
        self._assert_transition(
            ConnectionState.AUTH_ERROR,
            Trigger.DISCONNECT_REQUESTED,
            ConnectionState.DISCONNECTED,
            [Intent.CANCEL_TASKS, Intent.EMIT_DISCONNECTED],
        )

    def test_unreachable_connect_requested(self):
        self._assert_transition(
            ConnectionState.UNREACHABLE,
            Trigger.CONNECT_REQUESTED,
            ConnectionState.CONNECTING,
            [Intent.START_INITIAL_CONNECT],
        )

    def test_unreachable_disconnect_requested(self):
        self._assert_transition(
            ConnectionState.UNREACHABLE,
            Trigger.DISCONNECT_REQUESTED,
            ConnectionState.DISCONNECTED,
            [Intent.CANCEL_TASKS, Intent.EMIT_DISCONNECTED],
        )


class NoOpTransitionTest(unittest.TestCase):
    """Representative unlisted pairs return [] and leave the state unchanged (F1-F4, F6)."""

    def _assert_no_op(self, state: ConnectionState, trigger: Trigger) -> None:
        fsm = ConnectionFsm(state)
        result = fsm.apply(trigger)
        self.assertEqual(result, [])
        self.assertEqual(fsm.state, state)

    def test_connected_transport_available(self):
        """F1: duplicate is_available(True) callback while connected."""
        self._assert_no_op(ConnectionState.CONNECTED, Trigger.TRANSPORT_AVAILABLE)

    def test_reconnecting_transport_lost(self):
        """F1: duplicate is_available(False) callback while reconnecting."""
        self._assert_no_op(ConnectionState.RECONNECTING, Trigger.TRANSPORT_LOST)

    def test_connecting_transport_lost(self):
        """F2: is_available(False) during initial connect; the connect loop owns retries."""
        self._assert_no_op(ConnectionState.CONNECTING, Trigger.TRANSPORT_LOST)

    def test_connected_grace_elapsed(self):
        """F3: grace timer fires after the device already reconnected."""
        self._assert_no_op(ConnectionState.CONNECTED, Trigger.GRACE_ELAPSED)

    def test_disconnected_grace_elapsed(self):
        """F4: grace timer fires after an explicit disconnect()."""
        self._assert_no_op(ConnectionState.DISCONNECTED, Trigger.GRACE_ELAPSED)

    def test_auth_error_reconnect_auth_failed(self):
        """F6: re-entry into AUTH_ERROR is a no-op."""
        self._assert_no_op(ConnectionState.AUTH_ERROR, Trigger.RECONNECT_AUTH_FAILED)

    def test_auth_error_transport_available(self):
        self._assert_no_op(ConnectionState.AUTH_ERROR, Trigger.TRANSPORT_AVAILABLE)

    def test_connecting_connect_requested(self):
        """connect() while already connecting is idempotent."""
        self._assert_no_op(ConnectionState.CONNECTING, Trigger.CONNECT_REQUESTED)

    def test_connected_connect_requested(self):
        """connect() while already connected is idempotent."""
        self._assert_no_op(ConnectionState.CONNECTED, Trigger.CONNECT_REQUESTED)

    def test_disconnected_disconnect_requested(self):
        """disconnect() while already disconnected is idempotent."""
        self._assert_no_op(ConnectionState.DISCONNECTED, Trigger.DISCONNECT_REQUESTED)

    def test_ignored_transition_logged_at_debug(self):
        fsm = ConnectionFsm(ConnectionState.CONNECTED)
        with self.assertLogs("src.connection_fsm", level="DEBUG") as logs:
            fsm.apply(Trigger.TRANSPORT_AVAILABLE)
        self.assertTrue(any("Ignored transition" in message for message in logs.output))


class FlickerSuppressionTest(unittest.TestCase):
    """INV-5: a transport loss recovering within grace never surfaces DISCONNECTED."""

    def test_sub_grace_recovery_emits_reconnecting_once_and_no_disconnected(self):
        fsm = ConnectionFsm(ConnectionState.CONNECTED)
        intents = fsm.apply(Trigger.TRANSPORT_LOST)
        intents += fsm.apply(Trigger.TRANSPORT_AVAILABLE)
        self.assertEqual(fsm.state, ConnectionState.CONNECTED)
        self.assertEqual(intents.count(Intent.EMIT_RECONNECTING), 1)
        self.assertNotIn(Intent.EMIT_DISCONNECTED, intents)

    def test_grace_elapsed_before_recovery_emits_exactly_one_disconnected(self):
        fsm = ConnectionFsm(ConnectionState.CONNECTED)
        intents = fsm.apply(Trigger.TRANSPORT_LOST)
        intents += fsm.apply(Trigger.GRACE_ELAPSED)
        self.assertEqual(fsm.state, ConnectionState.RECONNECTING)
        self.assertEqual(intents.count(Intent.EMIT_DISCONNECTED), 1)

    def test_recovery_after_grace_emits_connected(self):
        fsm = ConnectionFsm(ConnectionState.CONNECTED)
        fsm.apply(Trigger.TRANSPORT_LOST)
        fsm.apply(Trigger.GRACE_ELAPSED)
        intents = fsm.apply(Trigger.TRANSPORT_AVAILABLE)
        self.assertEqual(fsm.state, ConnectionState.CONNECTED)
        self.assertIn(Intent.EMIT_CONNECTED, intents)


class TerminalStateTest(unittest.TestCase):
    """INV-6: from AUTH_ERROR/UNREACHABLE only CONNECT_REQUESTED and DISCONNECT_REQUESTED change state."""

    def test_only_explicit_user_action_leaves_terminal_states(self):
        for state in (ConnectionState.AUTH_ERROR, ConnectionState.UNREACHABLE):
            for trigger in Trigger:
                with self.subTest(state=state, trigger=trigger):
                    fsm = ConnectionFsm(state)
                    fsm.apply(trigger)
                    if trigger in (Trigger.CONNECT_REQUESTED, Trigger.DISCONNECT_REQUESTED):
                        self.assertNotEqual(fsm.state, state)
                    else:
                        self.assertEqual(fsm.state, state)

    def test_terminal_states_trigger_no_reconnect_intents(self):
        for state in (ConnectionState.AUTH_ERROR, ConnectionState.UNREACHABLE):
            for trigger in (Trigger.TRANSPORT_LOST, Trigger.GRACE_ELAPSED, Trigger.RECONNECT_AUTH_FAILED):
                with self.subTest(state=state, trigger=trigger):
                    fsm = ConnectionFsm(state)
                    self.assertEqual(fsm.apply(trigger), [])


class DefaultsTest(unittest.TestCase):
    """Construction defaults."""

    def test_initial_state_is_disconnected(self):
        self.assertEqual(ConnectionFsm().state, ConnectionState.DISCONNECTED)


class SansIoPurityTest(unittest.TestCase):
    """AC-10: src/connection_fsm.py imports only enum and logging (static import check)."""

    def test_module_imports_only_enum_and_logging(self):
        source = Path(__file__).resolve().parent.parent / "src" / "connection_fsm.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertIsNotNone(node.module, "relative imports are not allowed")
                imported.add(str(node.module).split(".")[0])
        self.assertLessEqual(imported, {"enum", "logging"}, f"unexpected imports: {imported}")


if __name__ == "__main__":
    unittest.main()
