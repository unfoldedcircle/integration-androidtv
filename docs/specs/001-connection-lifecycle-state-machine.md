# 001 — Connection lifecycle state machine

| | |
|---|---|
| **Status** | Approved |
| **Author** | Markus Zehnder (drafted with Claude) |
| **Date** | 2026-07-16 |
| **Target version** | 0.10.0 |
| **Affected components** | `src/connection_fsm.py` (new), `src/tv.py`, `src/driver.py`, `tests/` |
| **Depends on** | Branch `fix/reconnection-single-owner` (Stage-1 stabilization) merged to `main` — see [Sequencing](#sequencing-relative-to-the-stage-1-fixes) |
| **Origin** | `REVIEW-Claude-20260715.md` §4 (Stage 2) |

---

## Summary

`AndroidTv` (in `src/tv.py`) tracks its runtime connection with a single `IntEnum`
field (`DeviceState`) that is assigned from **20 different places**, including an
`androidtvremote2` library callback that mutates it from outside the connect loop
and races `connect()`/`disconnect()`. The same enum conflates three unrelated
lifecycles (one-time init, pairing, runtime connection). There are no unit tests for
this module.

This spec replaces the **runtime-connection** portion with a small, explicit,
**pure** finite state machine (`ConnectionState` + `ConnectionFsm`) that is the single
source of truth for connection state and the single emitter of connection `Events`.
Pairing and one-time init state are **out of scope** and keep using the existing
`DeviceState` enum (they are consumed by the setup flow — see Current State Analysis).

The FSM is a pure `(state, trigger) -> (next_state, [intent])` function. All side
effects (starting tasks, emitting events, calling the library) are expressed as
**intents** that a thin async executor in `AndroidTv` performs. This makes every
transition unit-testable with no event loop, no sockets, and no device.

This is Stage 2 of a two-stage plan. Stage 1 (`fix/reconnection-single-owner`)
already made the `androidtvremote2` library the single owner of reconnection and
introduced the helpers this spec builds on (`_has_live_connection`, the `_track`
task registry, the IP-rediscovery watcher). **This spec must be implemented on top of
Stage 1**, not instead of it.

### Normative invariants

These are safety-relevant and MUST hold after implementation. Each is covered by at
least one acceptance criterion and one test.

* **INV-1 — Single writer.** Runtime connection state is mutated **only** by
  `ConnectionFsm.apply()`. No code outside the FSM assigns the runtime connection
  state field. (Grep target: zero `self._conn_state = ` assignments outside the FSM
  module.)
* **INV-2 — Single reconnection owner.** Reconnection is owned exclusively by
  `androidtvremote2.keep_reconnecting()`. The driver runs **no** competing reconnect
  loop, and the command path **never** calls `connect()`. (Carried over from Stage 1;
  the FSM must not reintroduce a second owner.)
* **INV-3 — State/event coherence.** Connection `Events` (`CONNECTED`,
  `DISCONNECTED`, `AUTH_ERROR`) are emitted **only** by the FSM intent executor, so
  the emitted event and the state can never disagree.
* **INV-4 — Liveness, not power.** "Connected" is defined by a live transport
  (`_has_live_connection()`), never by the cached `is_on` flag.
* **INV-5 — No flicker.** A transport loss that recovers within the grace period
  (`RECONNECT_GRACE`) MUST NOT surface `DISCONNECTED` (UI → unavailable). Surfacing a
  distinct `RECONNECTING` signal during the grace window is allowed (and intended).
* **INV-6 — Terminal states are terminal.** `AUTH_ERROR` and `UNREACHABLE` trigger
  no automatic reconnect; they leave only on an explicit user action (re-pair,
  reconfigure, or manual retry).
* **INV-7 — Deterministic teardown.** `disconnect()` and device removal cancel every
  background task the device owns (initial-connect, grace timer, IP watcher, callback
  tasks).

---

## Current State Analysis

All references are against `main` @ `2c0ce32` **with `fix/reconnection-single-owner`
applied** (that branch is a prerequisite; line numbers below are approximate and will
have shifted — locate by symbol).

### The enum conflates three lifecycles

`DeviceState` (`src/tv.py:86`) has 14 members spanning:

* **init:** `IDLE`, `INITIALIZING`, `INITIALIZED`, `TIMEOUT`
* **pairing:** `START_PAIRING`, `PAIRING_STARTED`, `FINISH_PAIRING`, `FINISHED_PAIRING`, `PAIRING_ERROR`
* **runtime connection:** `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `ERROR`, `AUTH_ERROR`

`AUTH_ERROR` and `TIMEOUT` are shared between the init/pairing and runtime concerns.

### 20 write sites, one of them racy

`grep -n "self._state = " src/tv.py` → 20 hits (`init` 263/290/302/315; `start_pairing`
433/435/438/442; `finish_pairing` 456/458/461; `connect` 491/516/522/534/547/551/565;
`disconnect` 655; `_is_available_updated` 781). The worst is `_is_available_updated`
(`src/tv.py:778`), an `androidtvremote2` callback that flips the field between
`CONNECTED` and `CONNECTING` **from outside the connect loop**, concurrently with
`connect()`/`disconnect()`. The command decorator (`async_handle_atvlib_errors`,
`src/tv.py:137`) then makes routing decisions off this racy field.

### External consumers (defines the scope boundary)

* **Runtime connection state** is read only *internally* — the command decorator reads
  `self.state` to gate commands on `DeviceState.CONNECTED`. → **In scope.**
* **Pairing/init state** is read *externally* by the setup flow:
  * `src/setup_flow.py:552` — `state = _pairing_android_tv.state`
  * `src/setup_flow.py:623-627` — `_setup_error_from_device_state(state)` matches
    `tv.DeviceState.AUTH_ERROR` and `tv.DeviceState.TIMEOUT`.
  These run right after `init()` / pairing, before the runtime FSM starts. →
  **Out of scope; `DeviceState` stays for these.**
* `driver.py` and `voice_command.py` read `.is_on` / `.player_state`, **not** the
  connection state enum. → Unaffected.

### Driver-side event contract (must be preserved)

`_add_configured_android_tv` (`src/driver.py:283-287`) registers listeners for exactly
five events: `CONNECTED`, `DISCONNECTED`, `AUTH_ERROR`, `UPDATE`, `IP_ADDRESS_CHANGED`.

* `handle_connected` (`driver.py:141`) — flips entities from `UNAVAILABLE`→`UNKNOWN`
  (only if currently unavailable), clears `auth_error`, sets device state CONNECTED.
* `handle_disconnected` (`driver.py:164`) — sets entities `UNAVAILABLE`.
* `handle_authentication_error` (`driver.py:175`) — persists `auth_error=True`, sets
  entities `UNAVAILABLE`.

Two known dead artifacts (from the review, P2-1) to clean up under this spec:
`Events.CONNECTING` is emitted but has **no** driver listener; `Events.PAIRED` is
defined but never emitted.

---

## Design

### States

```python
class ConnectionState(IntEnum):
    DISCONNECTED = 0   # idle: no connection, not trying (initial, or after explicit disconnect/standby)
    CONNECTING   = 1   # initial connect in progress (bounded retry loop)
    CONNECTED    = 2   # transport live and available
    RECONNECTING = 3   # was connected, transport lost; library keep_reconnecting() is retrying
    AUTH_ERROR   = 4   # needs re-pairing; terminal until reconfigured (INV-6)
    UNREACHABLE  = 5   # initial connect gave up (max_timeout/fatal); terminal until user retry (INV-6)
```

### Triggers (the only inputs to the FSM)

```python
class Trigger(IntEnum):
    CONNECT_REQUESTED     = 0   # connect() called (user/driver/standby-exit/subscribe)
    CONNECT_SUCCEEDED     = 1   # initial async_connect() returned
    CONNECT_FAILED_AUTH   = 2   # InvalidAuth during initial connect
    CONNECT_ABORTED       = 3   # max_timeout reached or fatal error during initial connect
    TRANSPORT_LOST        = 4   # is_available(False) library callback
    TRANSPORT_AVAILABLE   = 5   # is_available(True) library callback
    RECONNECT_AUTH_FAILED = 6   # keep_reconnecting() invalid-auth callback
    GRACE_ELAPSED         = 7   # RECONNECTING grace timer fired without recovery
    DISCONNECT_REQUESTED  = 8   # disconnect() called (user/standby/removal)
```

### Intents (side effects the executor performs — the FSM itself performs none)

```python
class Intent(IntEnum):
    START_INITIAL_CONNECT   = 0   # spawn the bounded initial-connect coroutine
    START_KEEP_RECONNECTING = 1   # self._atv.keep_reconnecting(invalid_auth_cb)
    START_CAST              = 2   # await self._chromecast_connect()
    START_IP_WATCHER        = 3   # spawn _rediscover_ip_while_disconnected() (Stage-1 helper)
    START_GRACE_TIMER       = 4   # spawn grace timer -> dispatch GRACE_ELAPSED
    CANCEL_IP_WATCHER       = 5
    CANCEL_GRACE_TIMER      = 6
    CANCEL_TASKS            = 7   # cancel all tracked tasks (INV-7)
    ATV_DISCONNECT          = 8   # self._atv.disconnect()
    EMIT_CONNECTED          = 9   # events.emit(Events.CONNECTED, id)
    EMIT_DISCONNECTED       = 10  # events.emit(Events.DISCONNECTED, id)
    EMIT_AUTH_ERROR         = 11  # events.emit(Events.AUTH_ERROR, id)
    EMIT_RECONNECTING       = 12  # events.emit(Events.RECONNECTING, id)  -- new event, see Q3
```

A new `Events.RECONNECTING` member is added to the `Events` enum in `tv.py`. It signals
"was connected, transport lost, retrying" without marking the entity unavailable. It is
expected to map to an integration-API event so the client can show a reconnecting
indication; wiring it on the driver side is Phase 3 (or a future update — emitting it
with no listener is a harmless no-op in the meantime).

### Transition table (single source of truth)

Any `(state, trigger)` pair not listed is a **no-op**: the FSM stays in the current
state, returns `[]`, and logs the ignored transition at DEBUG. This makes idempotent
callbacks (duplicate `is_available`, `connect()` while connecting) safe.

| From | Trigger | To | Intents |
|---|---|---|---|
| DISCONNECTED | CONNECT_REQUESTED | CONNECTING | `START_INITIAL_CONNECT` |
| CONNECTING | CONNECT_SUCCEEDED | CONNECTED | `START_KEEP_RECONNECTING`, `START_CAST`, `EMIT_CONNECTED` |
| CONNECTING | CONNECT_FAILED_AUTH | AUTH_ERROR | `EMIT_AUTH_ERROR` |
| CONNECTING | CONNECT_ABORTED | UNREACHABLE | `EMIT_DISCONNECTED` |
| CONNECTING | DISCONNECT_REQUESTED | DISCONNECTED | `CANCEL_TASKS`, `ATV_DISCONNECT`, `EMIT_DISCONNECTED` |
| CONNECTED | TRANSPORT_LOST | RECONNECTING | `START_IP_WATCHER`, `START_GRACE_TIMER`, `EMIT_RECONNECTING` |
| CONNECTED | DISCONNECT_REQUESTED | DISCONNECTED | `CANCEL_TASKS`, `ATV_DISCONNECT`, `EMIT_DISCONNECTED` |
| RECONNECTING | TRANSPORT_AVAILABLE | CONNECTED | `CANCEL_IP_WATCHER`, `CANCEL_GRACE_TIMER`, `START_CAST`, `EMIT_CONNECTED` |
| RECONNECTING | GRACE_ELAPSED | RECONNECTING | `EMIT_DISCONNECTED` |
| RECONNECTING | RECONNECT_AUTH_FAILED | AUTH_ERROR | `CANCEL_IP_WATCHER`, `CANCEL_GRACE_TIMER`, `EMIT_AUTH_ERROR` |
| RECONNECTING | DISCONNECT_REQUESTED | DISCONNECTED | `CANCEL_TASKS`, `ATV_DISCONNECT`, `EMIT_DISCONNECTED` |
| AUTH_ERROR | CONNECT_REQUESTED | CONNECTING | `START_INITIAL_CONNECT` |
| AUTH_ERROR | DISCONNECT_REQUESTED | DISCONNECTED | `CANCEL_TASKS`, `EMIT_DISCONNECTED` |
| UNREACHABLE | CONNECT_REQUESTED | CONNECTING | `START_INITIAL_CONNECT` |
| UNREACHABLE | DISCONNECT_REQUESTED | DISCONNECTED | `CANCEL_TASKS`, `EMIT_DISCONNECTED` |

Notes on deliberate design choices:

* **Flicker suppression (INV-5).** `CONNECTED → RECONNECTING` emits a distinct
  `RECONNECTING` signal but **no** `DISCONNECTED`; it also arms the grace timer. The
  UI-facing `DISCONNECTED` (→ unavailable) is emitted *only* by
  `RECONNECTING + GRACE_ELAPSED`. If the library recovers first,
  `RECONNECTING + TRANSPORT_AVAILABLE` cancels the timer, emits `CONNECTED`, and the
  entity never went unavailable.
* **Grace duration.** `RECONNECT_GRACE = 1.5s`. The client applies a ~2–3s request
  timeout to the integration, so the grace window must stay comfortably below it; 1.5s
  also sits above the library's 0.1s initial backoff so most real blips heal inside it.
  Add a code comment that this value is provisional and needs field testing/tuning.
* **Idempotent recovery.** `CONNECTED + TRANSPORT_AVAILABLE` is a no-op. Re-emitting
  `EMIT_CONNECTED` on `RECONNECTING → CONNECTED` is harmless: `handle_connected` only
  changes state when it is currently `UNAVAILABLE`, and normal attribute updates
  follow.
* **`START_CAST` on reconnect.** Google Cast is a separate socket that also dropped;
  re-establishing it on `RECONNECTING → CONNECTED` mirrors the current power-on
  behaviour and is safe/off-loop after Stage-1's `fix/chromecast-off-event-loop`. If
  that branch is not yet merged, `START_CAST` still works but blocks — see Sequencing.

### Pure FSM API (sans-I/O)

This FSM follows the **sans-I/O** pattern: it performs **no** network, timer, task, or
event-emitter I/O of its own. It is a pure transformation — you feed it a `Trigger` and
it returns the `Intent`s the caller must perform, mutating only its own in-memory
`state`. All actual I/O (sockets via `androidtvremote2`, `asyncio` tasks/timers, event
emission) lives in the `AndroidTv` executor, on the far side of the `Intent` boundary.
Consequences that this spec relies on:

* `src/connection_fsm.py` imports **only** `enum`/`logging` — no `asyncio`, no
  `pychromecast`, no `androidtvremote2`, no import from `tv.py`. (Enforced by AC-10.)
* `ConnectionFsm.apply()` is synchronous and side-effect-free except for the in-memory
  state update, so the **entire transition table is exercised by plain synchronous unit
  tests with no event loop, no clock, no sockets, and no fakes** (`tests/test_connection_fsm.py`).
* Timing (the grace window) is *not* inside the FSM: the FSM only reacts to the
  `GRACE_ELAPSED` trigger, which tests inject directly. No `sleep`, no fake clock needed
  to test the timing-related transitions.

`src/connection_fsm.py` — no imports from `tv.py`, no asyncio, no I/O.

```python
class ConnectionFsm:
    def __init__(self, state: ConnectionState = ConnectionState.DISCONNECTED) -> None: ...

    @property
    def state(self) -> ConnectionState: ...

    def apply(self, trigger: Trigger) -> list[Intent]:
        """Apply a trigger. Pure except for updating self._state.

        Returns the ordered list of intents the caller must execute. Unlisted
        (state, trigger) pairs are no-ops: state unchanged, returns [], logged at DEBUG.
        """
```

The table is a module-level `dict[tuple[ConnectionState, Trigger], tuple[ConnectionState, tuple[Intent, ...]]]`.

### Integration into `AndroidTv` (the thin async executor)

* Add `self._conn = ConnectionFsm()` and a `connection_state` property returning
  `self._conn.state`.
* Add a single dispatch method — the **only** place the FSM is driven and the **only**
  place connection events are emitted (INV-1, INV-3):

  ```python
  def _dispatch(self, trigger: Trigger) -> None:
      for intent in self._conn.apply(trigger):
          self._execute(intent)

  def _execute(self, intent: Intent) -> None:
      # maps Intent -> concrete action: self._track(...) to start tasks,
      # self.events.emit(...) for EMIT_*, self._atv.keep_reconnecting/disconnect, etc.
      ...
  ```

* Rewire the runtime call sites to dispatch triggers instead of writing `self._state`:
  * `connect()` — on entry `_dispatch(CONNECT_REQUESTED)`; the bounded initial-connect
    loop (kept from Stage 1) dispatches `CONNECT_SUCCEEDED` / `CONNECT_FAILED_AUTH` /
    `CONNECT_ABORTED`. The `START_INITIAL_CONNECT` intent is what launches that loop
    (guard against re-entry with the existing `_connect_lock`).
  * `disconnect()` — `_dispatch(DISCONNECT_REQUESTED)`. The `CANCEL_TASKS` intent
    subsumes the Stage-1 task cancellation; keep the Chromecast teardown as-is.
  * `_is_available_updated(is_available)` — becomes exactly:
    `self._dispatch(Trigger.TRANSPORT_AVAILABLE if is_available else Trigger.TRANSPORT_LOST)`.
    The `START_IP_WATCHER` intent replaces Stage-1's inline watcher start; the watcher
    coroutine and constants from Stage 1 are reused unchanged.
  * `keep_reconnecting()`'s invalid-auth callback — `_dispatch(RECONNECT_AUTH_FAILED)`.
* The command decorator (`async_handle_atvlib_errors`) reads `self._conn.state` and
  gates on `ConnectionState.CONNECTED`; the `AUTH_ERROR` branch maps to
  `ConnectionState.AUTH_ERROR`. It continues to also check `_has_live_connection()`
  (Stage 1). It must not call `connect()` (INV-2, carried from Stage 1).

### What stays on `DeviceState` (explicitly out of scope)

`init()`, `start_pairing()`, `finish_pairing()` keep writing `DeviceState`
(`INITIALIZING`/`INITIALIZED`/`TIMEOUT`/`START_PAIRING`…/`PAIRING_ERROR`/`AUTH_ERROR`),
and `setup_flow.py` keeps reading `.state`. The runtime connection members
(`DISCONNECTED`/`CONNECTING`/`CONNECTED`/`ERROR`) become unused by runtime code once
the FSM lands; leave them defined for now to avoid touching the setup flow. Fully
retiring pairing/init from `DeviceState` into `SetupSteps` is a **follow-up spec**
(Decision 2), not part of 001.

---

## Failure Mode Analysis

| # | Failure mode | Mitigation in this design |
|---|---|---|
| F1 | Duplicate `is_available` callbacks (library fires True/True or False/False) | Same-state triggers are no-ops (table); executor performs no duplicate side effects. |
| F2 | `is_available(False)` arrives *during* initial `CONNECTING` | `CONNECTING + TRANSPORT_LOST` is unlisted → no-op; the initial-connect loop owns retries. |
| F3 | Grace timer fires after the device already reconnected | `CONNECTED + GRACE_ELAPSED` is unlisted → no-op; timer result is harmless if late. |
| F4 | Grace timer fires after an explicit `disconnect()` | On `DISCONNECT_REQUESTED` the executor cancels the grace task (`CANCEL_TASKS`); a race where it already fired lands in `DISCONNECTED + GRACE_ELAPSED` = no-op. |
| F5 | `disconnect()` mid-initial-connect | `CONNECTING + DISCONNECT_REQUESTED` cancels tasks and lands `DISCONNECTED`; the initial-connect coroutine must check for cancellation and not re-dispatch success. |
| F6 | Auth failure surfaces via both the initial connect and the reconnect callback | Both map to `AUTH_ERROR` (terminal, INV-6); re-entry into `AUTH_ERROR` is a no-op. |
| F7 | IP watcher keeps running after reconnect/auth-error | `CANCEL_IP_WATCHER` on `→CONNECTED` and `→AUTH_ERROR`; `CANCEL_TASKS` on disconnect (INV-7). |
| F8 | Executor raises while performing an intent | `_execute` must be defensive: a failing emit/task-start must be logged and must not corrupt `self._conn.state` (state is already committed by `apply()` before intents run). |
| F9 | Library internals change (`_remote_message_protocol`, `keep_reconnecting`) | Liveness is centralised in `_has_live_connection()` (Stage 1); pin `androidtvremote2==0.3.1` and re-verify on bump (Decision 5). |
| F10 | Transient SSL error misclassified as auth failure: the library maps **any** `ssl.SSLError` during connect to `InvalidAuth` (`androidtv_remote.py:243`, `:256` in 0.3.1) — e.g. `SSLEOFError` from a link drop mid-TLS-handshake terminates `keep_reconnecting()` with a spurious auth error | Lands in terminal `AUTH_ERROR` (INV-6); recovery is the next `CONNECT_REQUESTED` (exit-standby / entity subscribe). Same behaviour as pre-FSM code — not a regression. Watch field logs; if it occurs in practice, harden by retrying once in the auth callback before dispatching `RECONNECT_AUTH_FAILED` (would be a spec update). |
| F11 | The library reconnect task dies silently on an unexpected exception (e.g. `PermissionError` reading cert files escapes `_create_ssl_context`, which only catches `FileNotFoundError`); historic precedent: in 0.0.14 an escaping `ConnectionClosed` killed the loop (fixed upstream in `d976bb1`, included in 0.3.1) | **Phase 2 supervises the owner:** after `keep_reconnecting()`, attach a done-callback to `_atv._reconnect_task` (internals pin per Decision 5). If the task terminates without cancellation and without the auth callback having fired, log an error and re-establish ownership via the existing table: dispatch `DISCONNECT_REQUESTED` then `CONNECT_REQUESTED` (no new triggers/rows needed). |

---

## Test Plan

New: `tests/test_connection_fsm.py` (pure) and `tests/test_tv_connection.py`
(executor + `AndroidTv` with a fake library). Existing suite: `python -m unittest
discover tests` (unittest, no pytest). Both files ship **in the same phase as the code
they test** (README rule).

### `tests/test_connection_fsm.py` — pure, no event loop

1. **Transition table coverage** — one test per listed row asserts `(next_state, intents)`.
2. **No-op coverage** — a representative set of unlisted pairs return `[]` and leave
   state unchanged (F1–F4, F6): e.g. `CONNECTED+TRANSPORT_AVAILABLE`,
   `CONNECTING+TRANSPORT_LOST`, `AUTH_ERROR+TRANSPORT_AVAILABLE`,
   `DISCONNECTED+GRACE_ELAPSED`.
3. **Flicker (INV-5)** — `CONNECTED→(TRANSPORT_LOST)→RECONNECTING→(TRANSPORT_AVAILABLE)→CONNECTED`
   emits `EMIT_RECONNECTING` once and **no** `EMIT_DISCONNECTED`; the same path with
   `GRACE_ELAPSED` before recovery emits exactly one `EMIT_DISCONNECTED`.
4. **Terminality (INV-6)** — from `AUTH_ERROR`/`UNREACHABLE`, only `CONNECT_REQUESTED`
   and `DISCONNECT_REQUESTED` change state.

### `tests/test_tv_connection.py` — `AndroidTv` with a fake `AndroidTVRemote`

Provide a `FakeAndroidTVRemote` stub exposing: `host`, `is_on`, `async_connect`
(configurable to succeed / raise `InvalidAuth` / raise `CannotConnect`), `disconnect`,
`keep_reconnecting(cb)` (records `cb`), the four `add_*_callback` registrars, and a
settable `_remote_message_protocol` whose `transport.is_closing()` is controllable (so
`_has_live_connection()` can be steered). No network.

1. **Initial success** — `connect()` → `keep_reconnecting` called exactly once;
   `CONNECTED` emitted exactly once; `connection_state == CONNECTED` (INV-2/INV-3).
2. **P0-3 regression** — `is_on=True` but `transport.is_closing()=True` →
   `_has_live_connection()` is False → `connect()` proceeds instead of short-circuiting
   (INV-4).
3. **P0-2 regression** — a command while `RECONNECTING` returns `SERVICE_UNAVAILABLE`,
   does **not** spawn a second connect, and does **not** cancel the library reconnect
   (INV-2).
4. **IP rediscovery (P1-1)** — while `RECONNECTING` with a changed discovered IP,
   `_atv.host` is updated and `IP_ADDRESS_CHANGED` is emitted.
5. **Flicker end-to-end (INV-5)** — drive `TRANSPORT_LOST` then `TRANSPORT_AVAILABLE`
   within grace (directly dispatch `GRACE_ELAPSED` to avoid real sleeps): `RECONNECTING`
   emitted, **no** `DISCONNECTED`; exceeding grace emits exactly one `DISCONNECTED`.
6. **Teardown (INV-7)** — `disconnect()` from each of `CONNECTING`/`CONNECTED`/
   `RECONNECTING` cancels all tracked tasks and lands in `DISCONNECTED`.
7. **Auth terminal (INV-6)** — `RECONNECT_AUTH_FAILED` → `AUTH_ERROR`, `AUTH_ERROR`
   emitted, no further reconnect; watcher/grace cancelled.
8. **Single-writer audit (INV-1)** — `grep` assertion in the test (or a lint step):
   no `self._conn_state = ` outside `connection_fsm.py`.
9. **Reconnect-owner supervision (F11)** — make the fake's `_reconnect_task` complete
   with an exception (no cancellation, no auth callback): an error is logged and
   `DISCONNECT_REQUESTED` + `CONNECT_REQUESTED` are dispatched (a fresh
   `keep_reconnecting` is started); a *cancelled* task triggers nothing.

---

## Implementation Plan

Phases are ordered by dependency. Phase 1 is file-disjoint from everything (new files)
and could even begin before the Stage-1 branches merge. Phases 2–3 touch `tv.py` /
`driver.py` and therefore require Stage 1 merged first (see Sequencing).

### Phase 1 — Pure FSM core + its unit tests *(serial root; new files only)*

* **Files:** `src/connection_fsm.py` (new), `tests/test_connection_fsm.py` (new).
* **Work:** `ConnectionState`, `Trigger`, `Intent`, the transition-table dict,
  `ConnectionFsm.apply()`, DEBUG logging of ignored transitions; full pure test suite
  (Test Plan §`test_connection_fsm.py`).
* **Dependencies:** none. File-disjoint → dispatchable to a worktree agent immediately,
  in parallel with the Stage-1 branches.
* **Mergeable on its own:** yes (adds an unused-but-tested module).

### Phase 2 — Integrate the FSM into `AndroidTv` + integration tests *(serial; single-file core refactor)*

* **Files:** `src/tv.py`, `tests/test_tv_connection.py` (new).
* **Work:** add `self._conn`, `connection_state`, `_dispatch`, `_execute`; convert the
  runtime `self._state` writes (`connect`, `disconnect`, `_is_available_updated`,
  `keep_reconnecting` auth cb) to trigger dispatches; point the command decorator at
  `self._conn.state`; wire the Stage-1 IP watcher + grace timer through intents;
  supervise the library reconnect owner (done-callback on `_atv._reconnect_task`, F11);
  add the fake-library integration tests.
* **Dependencies:** Phase 1; **Stage-1 `fix/reconnection-single-owner` merged**
  (reuses `_has_live_connection`, `_track`, `_rediscover_ip_while_disconnected`).
  Because it is a single-file core refactor it is a **serial dependency** for Phase 3
  and cannot be parallelised with other `tv.py` work.
* **Out of scope:** `init()`/pairing state, Google Cast internals.

### Phase 3 — Driver event-contract cleanup *(depends on Phase 2; then file-disjoint)*

* **Files:** `src/driver.py`, `src/tv.py` (enum-only edits).
* **Work:** remove dead `Events.PAIRED`; either wire `Events.CONNECTING`/a new
  `RECONNECTING` signal to a driver listener (if the UI should show "connecting"), or
  remove the dead emit (P2-1). Optionally surface `RECONNECTING` distinctly from
  `DISCONNECTED` in the UI. No behaviour change to the FSM.
* **Dependencies:** Phase 2 (final event contract). After Phase 2 lands, this is
  file-disjoint from further FSM work and can be a small standalone PR.

### CI

* Confirm the CI test job runs `python -m unittest discover tests` (add it if missing)
  so the two new test files gate merges. This is part of Phase 1's PR.
* Every phase must pass the repo's real gate before merge: `black --check
  --line-length 120`, `isort --check`, `flake8`, `pylint`, `unittest`. Additionally,
  keep new code **ruff + pyright(strict) clean** so it survives the outstanding
  toolchain migration (Decision 1) without rework — full type annotations, no star
  imports, no suppressions ruff would reject.

---

## Acceptance Criteria

* **AC-1 (INV-1):** `grep -n "self._conn_state = " src/` returns hits only inside
  `src/connection_fsm.py`; no runtime connection-state write exists elsewhere in `tv.py`.
* **AC-2 (INV-2):** `grep -n "connect()" src/tv.py` shows no call to `self.connect()`
  from the command path or any reconnect callback; `keep_reconnecting()` is the only
  reconnect owner.
* **AC-3 (INV-3):** every `Events.CONNECTED|DISCONNECTED|AUTH_ERROR` emit in `tv.py`
  originates from the FSM intent executor (single call site each).
* **AC-4 (INV-4):** the P0-3 regression test passes — stale `is_on=True` with a closed
  transport does not report connected.
* **AC-5 (INV-5):** the flicker tests pass — sub-grace recovery emits `RECONNECTING`
  and no `DISCONNECTED`; a blip exceeding `RECONNECT_GRACE` (1.5s) emits exactly one
  `DISCONNECTED`.
* **AC-6 (INV-6):** auth/unreachable terminality tests pass.
* **AC-7 (INV-7):** teardown tests pass — no task survives `disconnect()`.
* **AC-8 — FSM unit-test suite (sans-I/O, required deliverable):** `Phase 1` ships
  `tests/test_connection_fsm.py` covering **every row of the transition table** plus
  representative no-op pairs, and it runs with **no event loop, no clock, no sockets,
  and no fakes** (pure sans-I/O tests). This suite existing and passing is a hard gate —
  a state machine merged without it does not satisfy this spec.
* **AC-9 — integration suite:** `Phase 2` ships `tests/test_tv_connection.py` covering
  Test-Plan items 1–8; `python -m unittest discover tests` is green, including both new
  files, and the full lint gate is green.
* **AC-10 (sans-I/O purity):** `src/connection_fsm.py` imports only stdlib
  (`enum`, `logging`); it imports no `asyncio`, `pychromecast`, `androidtvremote2`, or
  `tv`. (Assertable by a static import check in the test suite.)
* **AC-11:** the setup flow is unchanged and still resolves auth/timeout errors
  (`setup_flow._setup_error_from_device_state` still works via `DeviceState`).

---

## Decisions & Open Questions

Resolved during review (2026-07-16); recorded here for traceability.

1. **Toolchain migration (resolved).** The mismatch between `docs/specs/README.md`
   (`./lint.sh` → `ruff check` / `ruff format --check` / `pyright` strict, plus
   `i18n.py`) and the current repo gate (`black`/`isort`/`flake8`/`pylint` + `unittest`,
   no `i18n.py`) is because a migration to **ruff + pyright** is outstanding but not yet
   landed. This spec is written to be compatible with **both**: implement against the
   current gate, but keep the new code ruff/pyright-clean (full type annotations, no
   star imports, no lint suppressions that ruff would reject) so it needs no rework when
   the migration lands. When ruff/pyright/`lint.sh` are in place, the README applies
   verbatim. The FSM has no user-facing strings (log messages only, which are never
   translated), so the `i18n.py` rule does not block 001.
2. **Retire pairing/init from `DeviceState` (resolved: separate spec).** The clean end
   state — `ConnectionState` for runtime + `SetupSteps` for setup, `DeviceState`
   deleted — will be its own follow-up spec (candidate **002**), not folded into 001.
   001 leaves `DeviceState` in place for `init()`/pairing.
3. **`RECONNECTING` event (resolved: keep it).** A distinct `Events.RECONNECTING` is
   added and emitted on `CONNECTED → RECONNECTING` (see Design). It is expected to map
   to an integration-API event so the client can show a reconnecting indication. Driver
   wiring / API mapping is Phase 3 or a future update; until then the emit is a harmless
   no-op.
4. **Grace duration (resolved: 1.5s, provisional).** `RECONNECT_GRACE = 1.5s`, chosen
   to stay comfortably under the client's ~2–3s request timeout to the integration. The
   implementation MUST carry a code comment that this value is provisional and requires
   field testing and adjustment.
5. **Library-internals pin (resolved: accepted).** The design depends on
   `androidtvremote2==0.3.1` (`keep_reconnecting`, `is_on` caching,
   `_remote_message_protocol.transport`). Dependence on the private
   `_remote_message_protocol` attribute is accepted, funnelled through the single
   `_has_live_connection()` chokepoint. Add a comment at the requirements pin and a
   checklist item to re-verify these internals on any `androidtvremote2` bump.
6. **Library reconnection verified (2026-07-16).** Source review of the pinned 0.3.1
   confirmed `_async_reconnect` retries **indefinitely** (backoff 0.1s → 30s cap) and
   exits only on `InvalidAuth`, `disconnect()`, or cancellation; a 16s idle watchdog
   (`remote.py:332`, server pings every 5s) detects silent drops (Wi-Fi loss, half-open
   TCP, device sleep) and bounds any hang inside `async_connect()`. The integration's
   historical second reconnect loop (PR #36 / issue #40, April 2024) compensated for
   androidtvremote2 **0.0.14**, whose loop caught only `CannotConnect` — an escaping
   `ConnectionClosed` silently killed it; fixed upstream in `d976bb1` (2024-04-28),
   included in 0.3.1. The single-owner model (INV-2) is therefore safe. Residual risks
   are recorded as F10/F11.
7. **No reconnect nudge from the command path (deliberate).** `CONNECT_REQUESTED` while
   `RECONNECTING` stays an unlisted no-op: the library owns retries (INV-2). Worst-case
   recovery after a silent drop is ~16s idle detection + ≤30s backoff; a command sent
   during a backoff wait returns `SERVICE_UNAVAILABLE` instead of forcing an immediate
   (race-prone) reconnect as the pre-FSM code did. If field testing shows this latency
   matters, add a race-free nudge transition (`RECONNECTING + CONNECT_REQUESTED →
   CONNECTING`) in a follow-up spec update.

_No open questions remain._
