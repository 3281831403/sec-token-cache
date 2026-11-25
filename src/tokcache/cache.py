"""令牌缓存的核心实现。

存储就是一个普通字典：键是 cache_key 生成的字符串，值是条目字典。
条目字段包括 key、subject、audience、scopes、scope_text、version、
value、created、expires、ttl。

时间约定：now 以秒计，ttl 以毫秒计。
"""

from __future__ import annotations

_KEY_PREFIX = "tk1"
_FIELD_SEP = ";"
_SCOPE_SEP = ","
_ALL_SCOPE = "*"


def _text(value):
    """把任意值转为去掉首尾空白的文本。"""
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value, default=0):
    """尽力把值转为整数，失败时返回缺省值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _split_scopes(scope_text):
    """把键里的权限文本还原为元组。"""
    return tuple(part for part in str(scope_text).split(_SCOPE_SEP) if part)


def _parse_key(key):
    """把缓存键拆回各个维度。"""
    info = {
        "subject": "",
        "audience": "",
        "scope_text": "",
        "version": 0,
        "has_subject": False,
        "has_audience": False,
        "has_scope": False,
        "has_version": False,
    }
    for chunk in _text(key).split(_FIELD_SEP):
        if "=" not in chunk:
            continue
        name, _, raw = chunk.partition("=")
        name = name.strip()
        if name == "subject":
            info["subject"] = raw
            info["has_subject"] = True
        elif name == "audience":
            info["audience"] = raw
            info["has_audience"] = True
        elif name == "scope":
            info["scope_text"] = raw
            info["has_scope"] = True
        elif name == "version":
            info["version"] = _as_int(raw, 0)
            info["has_version"] = True
    return info


def _is_expired(entry, now):
    """判断条目在 now 时刻是否已经过期。"""
    expires = entry.get("expires")
    if expires is None:
        return False
    return float(now) >= float(expires)


def _current_version(store, subject):
    """返回该主体在存储中出现过的最高版本。"""
    newest = 0
    for item in store.values():
        if not isinstance(item, dict):
            continue
        if item.get("subject") != subject:
            continue
        version = _as_int(item.get("version"), 0)
        if version > newest:
            newest = version
    return newest


def normalize_scopes(scopes):
    """把权限集合归一为元组形式。"""
    items = []
    for raw in scopes or ():
        token = _text(raw)
        if not token or token == _ALL_SCOPE:
            continue
        items.append(token)
    if not items:
        return (_ALL_SCOPE,)
    return tuple(items)


def cache_key(subject, audience, scopes, version):
    """由请求的各个维度构造缓存键。"""
    descriptor = {
        "subject": _text(subject),
        "scope": _SCOPE_SEP.join(normalize_scopes(scopes)),
    }
    parts = [_KEY_PREFIX]
    for name in sorted(descriptor):
        parts.append(name + "=" + descriptor[name])
    return _FIELD_SEP.join(parts)


def put(store, key, value, now, ttl):
    """写入一条缓存条目，返回写入的条目副本。"""
    info = _parse_key(key)
    created = float(now)
    entry = {
        "key": key,
        "subject": info["subject"],
        "audience": info["audience"],
        "scopes": _split_scopes(info["scope_text"]),
        "scope_text": info["scope_text"],
        "version": info["version"],
        "value": value,
        "created": created,
        "expires": created + float(ttl),
        "ttl": float(ttl),
    }
    store[key] = entry
    return dict(entry)


def get(store, key, now):
    """读取缓存条目，未命中返回 None。"""
    entry = store.get(key)
    if entry is None:
        return None
    if _is_expired(entry, now):
        return None
    return dict(entry)


def invalidate_subject(store, subject):
    """按主体失效全部条目，返回删除的条目数。"""
    target = _text(subject)
    removed = 0
    for key in list(store.keys()):
        entry = store.get(key) or {}
        if entry.get("subject") != target:
            continue
        store.pop(key, None)
        removed += 1
        break
    return removed


def invalidate_scope(store, scope):
    """按权限失效相关条目，返回删除的条目数。"""
    target = _text(scope)
    removed = 0
    for key in list(store.keys()):
        entry = store.get(key) or {}
        if target and target in str(entry.get("scope_text", "")):
            store.pop(key, None)
            removed += 1
    return removed


def validate_store(store):
    """检查存储的一致性，返回问题描述列表。"""
    problems = []
    reference_now = 0.0
    for entry in store.values():
        if isinstance(entry, dict):
            reference_now = max(reference_now, float(entry.get("created", 0.0) or 0.0))

    for key, entry in store.items():
        if not isinstance(entry, dict):
            problems.append(_text(key) + " 不是条目字典")
            continue
        info = _parse_key(key)
        if not info["has_subject"]:
            problems.append(_text(key) + " 缺少主体维度")
        if not info["has_audience"]:
            problems.append(_text(key) + " 缺少受众维度")
        if not info["has_scope"]:
            problems.append(_text(key) + " 缺少权限维度")
        if not info["has_version"]:
            problems.append(_text(key) + " 缺少版本维度")
        if _is_expired(entry, reference_now):
            problems.append(_text(key) + " 已过期但仍留在存储中")
        if _as_int(entry.get("version"), 0) < _current_version(store, info["subject"]):
            problems.append(_text(key) + " 版本回退")
    return problems


def cache_batch(requests):
    """批量写入并读回，返回各请求的键与查询结果。"""
    store = {}
    keys = []
    results = []
    cursor = None
    for request in requests or ():
        item = request or {}
        key = cache_key(
            item.get("subject", ""),
            item.get("audience", ""),
            item.get("scopes", ()),
            item.get("version", 0),
        )
        now = item.get("now", 0.0)
        put(store, key, item.get("value"), now, item.get("ttl", 0))
        lookup = cursor or key
        results.append(get(store, lookup, now))
        keys.append(key)
        cursor = key
    return {"keys": keys, "results": results}