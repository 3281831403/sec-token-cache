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
# now 以秒计、ttl 以毫秒计，写入过期时间时必须除以 1000。
_MILLISECONDS_PER_SECOND = 1000.0


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
    """把权限集合归一为有序、去重的元组形式。

    归一结果与书写顺序、重复次数无关；空集合保持为空元组，
    通配权限 ``*`` 原样保留，因此空权限与全部权限是两种不同情形。
    也接受用权限分隔符拼成的文本（如 ``"read,write"``）。
    """
    if scopes is None:
        raw_items = ()
    elif isinstance(scopes, str):
        raw_items = _split_scopes(scopes)
    else:
        raw_items = scopes
    items = set()
    for raw in raw_items or ():
        token = _text(raw)
        if not token:
            continue
        items.add(token)
    return tuple(sorted(items))


def cache_key(subject, audience, scopes, version):
    """由请求的各个维度构造缓存键。

    键必须同时包含主体、受众、归一后的完整权限集合以及版本，
    缺任何一个维度都会造成不同条目共用缓存。
    """
    descriptor = {
        "subject": _text(subject),
        "audience": _text(audience),
        "scope": _SCOPE_SEP.join(normalize_scopes(scopes)),
        "version": str(_as_int(version, 0)),
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
        # ttl 是毫秒，now/expires 是秒，必须先换算，不能直接相加。
        "expires": created + float(ttl) / _MILLISECONDS_PER_SECOND,
        "ttl": float(ttl),
    }
    store[key] = entry
    return dict(entry)


def get(store, key, now):
    """读取缓存条目，未命中返回 None。

    以下情形都视为未命中：键下没有条目、条目不是字典、已经过期、
    键中版本与条目版本不符，或者该主体在存储中已经出现更高版本
    （权限/版本前进后留下的旧条目必须立即不再对外提供）。
    """
    entry = store.get(key)
    if not isinstance(entry, dict):
        return None
    if _is_expired(entry, now):
        return None
    info = _parse_key(key)
    entry_version = _as_int(entry.get("version"), info["version"])
    if info["has_version"] and info["version"] != entry_version:
        return None
    subject = entry.get("subject", info["subject"])
    if entry_version < _current_version(store, subject):
        return None
    return dict(entry)


def invalidate_subject(store, subject):
    """按主体失效其全部条目，返回实际删除的条目数。"""
    target = _text(subject)
    removed = 0
    for key in list(store.keys()):
        entry = store.get(key)
        if isinstance(entry, dict):
            raw_subject = entry.get("subject")
            if raw_subject is None:
                raw_subject = _parse_key(key)["subject"]
        else:
            raw_subject = _parse_key(key)["subject"]
        if _text(raw_subject) != target:
            continue
        store.pop(key, None)
        removed += 1
    return removed


def _entry_scope_tuple(entry, key=""):
    """取条目的权限集合，并归一为有序去重元组。"""
    raw_scopes = None
    if isinstance(entry, dict):
        raw_scopes = entry.get("scopes")
        if raw_scopes is None:
            raw_scopes = entry.get("scope_text")
    if raw_scopes is None:
        raw_scopes = _parse_key(key)["scope_text"]
    return normalize_scopes(raw_scopes)


def invalidate_scope(store, scope):
    """按权限集合失效条目，返回实际删除的条目数。

    只删除权限集合归一后与目标完全相同的条目：顺序、重复写法不影响
    比较，但多一个或少一个权限都不算匹配，也不做子串/包含匹配。
    目标可以是权限序列，也可以是单个权限或逗号拼接的权限文本。
    """
    target = normalize_scopes(scope)
    removed = 0
    for key in list(store.keys()):
        entry = store.get(key)
        if not isinstance(entry, dict):
            continue
        if _entry_scope_tuple(entry, key) != target:
            continue
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
    """批量写入并读回，返回各请求各自的键与查询结果。

    逐条独立处理：每个请求只写入并按自己的键读回，不能复用上一条
    请求的键，避免相邻请求串结果。
    """
    store = {}
    keys = []
    results = []
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
        results.append(get(store, key, now))
        keys.append(key)
    return {"keys": keys, "results": results}