"""公开测试：数组缓存的基本契约。"""

from tokcache import (
    cache_batch,
    cache_key,
    get,
    normalize_scopes,
    put,
    validate_store,
)


def test_write_then_read_single_entry():
    store = {}
    key = cache_key("alice", "api", ("read",), 3)
    entry = put(store, key, {"role": "admin"}, now=1000.0, ttl=5000)
    assert entry["version"] == 3
    assert entry["expires"] == 1005.0
    hit = get(store, key, now=1002.0)
    assert hit is not None
    assert hit["value"] == {"role": "admin"}


def test_different_subjects_use_different_keys():
    store = {}
    key_alice = cache_key("alice", "api", ("read",), 1)
    key_bob = cache_key("bob", "api", ("read",), 1)
    assert key_alice != key_bob
    put(store, key_alice, "alice-value", now=10.0, ttl=60000)
    put(store, key_bob, "bob-value", now=10.0, ttl=60000)
    assert get(store, key_alice, now=11.0)["value"] == "alice-value"
    assert get(store, key_bob, now=11.0)["value"] == "bob-value"
    assert validate_store(store) == []


def test_scope_order_does_not_change_key():
    first = cache_key("alice", "api", ("read", "write"), 2)
    second = cache_key("alice", "api", ("write", "read"), 2)
    assert first == second
    store = {}
    put(store, first, "allowed", now=5.0, ttl=5000)
    assert get(store, second, now=6.0)["value"] == "allowed"
    assert normalize_scopes(("write", "read")) == ("read", "write")


def test_expired_entry_is_not_hit():
    store = {}
    key = cache_key("alice", "api", ("read",), 4)
    put(store, key, "cached", now=2000.0, ttl=500)
    assert get(store, key, now=2000.2)["value"] == "cached"
    assert get(store, key, now=2001.0) is None


def test_batch_requests_are_independent():
    payload = cache_batch(
        [
            {
                "subject": "alice",
                "audience": "api",
                "scopes": ("read",),
                "version": 1,
                "value": "alice-value",
                "now": 100.0,
                "ttl": 60000,
            },
            {
                "subject": "bob",
                "audience": "api",
                "scopes": ("write",),
                "version": 1,
                "value": "bob-value",
                "now": 100.0,
                "ttl": 60000,
            },
        ]
    )
    assert len(payload["results"]) == 2
    assert payload["keys"][0] != payload["keys"][1]
    assert payload["results"][0]["value"] == "alice-value"
    assert payload["results"][1]["value"] == "bob-value"