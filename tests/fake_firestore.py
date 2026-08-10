"""In-memory emulator of the small Firestore surface that ``bobcoin.bank`` uses.

Enough of ``AsyncClient`` / ``Query`` / ``async_transactional`` to run the bank
logic without network access or credentials. Not a full emulator:

- transactions apply writes immediately (no rollback), which is fine because
  every ``_txn`` in bank.py only writes *after* all its checks pass.
- ``async_transactional`` is a no-op identity decorator.
"""

import sys
import types
import uuid


class FakeSnapshot:
    """Mirrors Firestore DocumentSnapshot: ``to_dict()`` returns None if missing."""

    def __init__(self, data, exists=True, id=""):
        self._data = data
        self.exists = exists
        self.id = id

    def to_dict(self):
        return None if not self.exists else dict(self._data)


def _iter_docs(store, prefix):
    """Yield snapshots for immediate children of ``prefix`` (e.g. users/<id>)."""
    # iterate over a key snapshot so writes during iteration don't blow up
    for key in list(store.keys()):
        if key.startswith(prefix + "/") and key.count("/") == prefix.count("/") + 1:
            yield FakeSnapshot(store[key], True, key.rsplit("/", 1)[-1])


class FakeTransaction:
    def __init__(self, store):
        self._store = store

    async def get(self, ref, transaction=None):
        return ref._snapshot()

    def set(self, ref, data, merge=False):
        # Mirror real Firestore semantics: merge=True updates only the given
        # fields and PRESERVES all others (critical — bank code relies on this
        # to keep fields like loan_balance / xp / luck intact across writes).
        if merge and self._store.get(ref._key) is not None:
            self._store[ref._key].update(data)
        else:
            self._store[ref._key] = dict(data)


class FakeDocument:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    def _snapshot(self):
        data = self._store.get(self._key)
        return FakeSnapshot(data, data is not None, self._key.rsplit("/", 1)[-1])

    async def get(self, transaction=None):
        return self._snapshot()

    async def set(self, data, merge=False):
        if merge and self._store.get(self._key) is not None:
            self._store[self._key].update(data)
        else:
            self._store[self._key] = dict(data)

    def collection(self, name):
        return FakeCollection(self._store, f"{self._key}/{name}")


class FakeQuery:
    def __init__(self, store, prefix, order=None, direction=None, limit=None):
        self._store = store
        self._prefix = prefix
        self._order = order
        self._direction = direction
        self._limit = limit

    def order_by(self, field, direction=None):
        return FakeQuery(self._store, self._prefix, field, direction, self._limit)

    def limit(self, n):
        return FakeQuery(self._store, self._prefix, self._order, self._direction, n)

    async def stream(self):
        docs = list(_iter_docs(self._store, self._prefix))
        if self._order is not None:
            docs.sort(
                key=lambda d: d._data.get(self._order, 0),
                reverse=(self._direction == FakeQueryConst.DESCENDING),
            )
        if self._limit is not None:
            docs = docs[: self._limit]
        for d in docs:
            yield d


class FakeCollection:
    def __init__(self, store, prefix):
        self._store = store
        self._prefix = prefix

    def document(self, doc_id=None):
        return FakeDocument(self._store, f"{self._prefix}/{doc_id}")

    async def stream(self):
        for snap in _iter_docs(self._store, self._prefix):
            yield snap

    def order_by(self, field, direction=None):
        return FakeQuery(self._store, self._prefix, field, direction, None)

    def limit(self, n):
        return FakeQuery(self._store, self._prefix, None, None, n)

    async def add(self, data):
        key = f"{self._prefix}/{uuid.uuid4().hex}"
        self._store[key] = dict(data)
        return FakeDocument(self._store, key)


class FakeClient:
    """Stands in for google.cloud.firestore.AsyncClient."""

    def __init__(self, store=None, *args, **kwargs):
        self._store = store if store is not None else {}

    def collection(self, name):
        return FakeCollection(self._store, name)

    def transaction(self):
        return FakeTransaction(self._store)


class FakeQueryConst:
    ASCENDING = 1
    DESCENDING = -1


def install_fake_firestore():
    """Inject the fake ``google.cloud.firestore`` module into sys.modules.

    Must run before ``bobcoin.bank`` is imported.
    """
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    firestore = types.ModuleType("google.cloud.firestore")
    firestore.AsyncClient = FakeClient
    firestore.Query = FakeQueryConst
    firestore.async_transactional = lambda fn: fn  # no-op decorator
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.cloud", cloud)
    sys.modules["google.cloud.firestore"] = firestore
