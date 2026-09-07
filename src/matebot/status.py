"""Status snapshots shared by live WebSocket traffic and offline replay."""

from copy import deepcopy

# Local stream boundary, never sent to the machine. Consumers discard stale state.
DISCONNECTED = "matebot:disconnected"


class StatusAccumulator:
    """Merge top-level patches; absence retains a value and null clears it."""

    def __init__(self) -> None:
        self._state: dict = {}

    def apply(self, message: dict) -> dict:
        if message.get("tp") == DISCONNECTED:
            self._state.clear()
        if message.get("tp") != "evt:status":
            return message
        self._state.update(deepcopy(message))
        # Independent snapshots protect earlier frames, including nested process data.
        return deepcopy(self._state)
