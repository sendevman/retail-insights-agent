import pytest

from retail_agent.cli import Chat
from retail_agent.safety import SafeError


def populate(store):
    a = store.save("alice", "s1", "Client X", "Client X performance")
    b = store.save("alice", "s2", "Client Y", "Other report")
    c = store.save("bob", "s1", "Client X", "Client X performance")
    return a, b, c


def test_exact_snapshot_and_owner(store):
    a, b, c = populate(store)
    token, rows = store.prepare_delete("alice", "s1", "mention", "Client X", now=100)
    assert [r["id"] for r in rows] == [a]
    # A matching report created after preview must survive.
    new = store.save("alice", "s1", "Client X new", "Client X")
    assert store.confirm_delete("alice", "s1", token, now=110) == 1
    assert {r["id"] for r in store.list("alice")} == {b, new}
    assert [r["id"] for r in store.list("bob")] == [c]


@pytest.mark.parametrize(
    "owner,session,when", [("bob", "s1", 110), ("alice", "s2", 110), ("alice", "s1", 221)]
)
def test_wrong_owner_session_or_expiry_denied(store, owner, session, when):
    populate(store)
    token, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    with pytest.raises(SafeError):
        store.confirm_delete(owner, session, token, now=when)
    assert len(store.list("alice")) == 2


def test_replay_denied(store):
    populate(store)
    token, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    store.confirm_delete("alice", "s1", token, now=101)
    with pytest.raises(SafeError):
        store.confirm_delete("alice", "s1", token, now=102)


def test_changed_report_rolls_back_all(store):
    populate(store)
    second = store.save("alice", "s1", "Second", "body")
    token, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    store.db.execute("UPDATE reports SET version=version+1 WHERE id=?", (second,))
    with pytest.raises(SafeError, match="changed"):
        store.confirm_delete("alice", "s1", token, now=101)
    assert len(store.list("alice")) == 3


def test_literal_wildcards_and_quote_safe(store):
    populate(store)
    for term in ("%", "_", "' OR 1=1 --"):
        token, rows = store.prepare_delete("alice", "s1", "mention", term)
        assert token is None
        assert rows == []


def test_cancel_and_new_preview_invalidate_old(store):
    populate(store)
    token, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    store.cancel("alice", "s1")
    with pytest.raises(SafeError):
        store.confirm_delete("alice", "s1", token, now=101)
    first, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    second, _ = store.prepare_delete("alice", "s1", "conversation", now=100)
    with pytest.raises(SafeError):
        store.confirm_delete("alice", "s1", first, now=101)
    assert store.confirm_delete("alice", "s1", second, now=101) == 1


def test_natural_delete_cannot_call_model(analyst, store, events):
    populate(store)
    chat = Chat(analyst, store, "alice", "s1", events)
    preview = chat.handle("Delete all the reports we made in this conversation")
    assert "/confirm " in preview
    assert len(store.list("alice")) == 2
    chat.handle("yes")
    token = preview.split("/confirm ")[1].splitlines()[0]
    assert "invalid" in chat.handle("/confirm " + token)
    assert len(store.list("alice")) == 2


def test_read_and_list_scoped_to_owner(store):
    a, _, c = populate(store)
    with pytest.raises(SafeError):
        store.read("alice", c)
    assert store.read("alice", a) == "Client X performance"
