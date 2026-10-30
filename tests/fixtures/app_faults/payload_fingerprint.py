"""Complete, privacy-preserving JSON fingerprints for passive QA observers."""

import hashlib
import hmac
import json

ALGORITHM = "cadsg-json-tree-hmac-sha256-v1"
MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 128
MAX_NODES = 100_000


class _Number(str):
    """Keep the JSON number's lexical representation without float conversion."""


class _Rejected(Exception):
    pass


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise _Rejected("duplicate_key")
        result[name] = value
    return result


def _nonfinite(_):
    raise _Rejected("non_finite_number")


def _frame(value):
    return len(value).to_bytes(8, "big") + value


def _new_mac(key, domain):
    return hmac.new(
        key, ALGORITHM.encode("ascii") + b"\0" + domain + b"\0", hashlib.sha256
    )


def _digest(key, domain, value):
    mac = _new_mac(key, domain)
    mac.update(_frame(value))
    return mac.digest()


def fingerprint(raw: bytes, key: bytes) -> dict:
    """Hash all fields and nodes, or return sanitized not_comparable metadata.

    JSON strings and keys are compared after escape decoding, without Unicode
    normalization. Insignificant JSON whitespace and object key order do not
    affect equality. Numbers retain their lexical spelling. Reuse one ephemeral
    in-memory key for all comparisons within the same case.
    """
    def rejected(reason):
        return {
            "status": "not_comparable",
            "algorithm": ALGORITHM,
            "reason": reason,
            "node_count": None,
        }

    if type(raw) is not bytes:
        return rejected("invalid_input")
    if type(key) is not bytes or len(key) < 32:
        return rejected("invalid_key")
    if len(raw) > MAX_BYTES:
        return rejected("byte_limit")

    nodes = {}
    count = 0

    def visit(value, path, depth):
        nonlocal count
        if depth > MAX_DEPTH:
            raise _Rejected("depth_limit")
        count += 1
        if count > MAX_NODES:
            raise _Rejected("node_limit")
        mac = _new_mac(key, b"node")
        children = 0
        extra = {}
        if isinstance(value, dict):
            kind = "object"
            children = len(value)
            mac.update(b"O" + children.to_bytes(8, "big"))
            keys_mac = _new_mac(key, b"keys")
            keys_mac.update(children.to_bytes(8, "big"))
            for name in sorted(value):
                encoded = name.encode("utf-8", errors="strict")
                framed = _frame(encoded)
                keys_mac.update(framed)
                mac.update(framed)
                escaped = name.replace("~", "~0").replace("/", "~1")
                mac.update(visit(value[name], path + "/" + escaped, depth + 1))
            extra["keys_digest"] = keys_mac.hexdigest()
        elif isinstance(value, list):
            kind = "array"
            children = len(value)
            mac.update(b"A" + children.to_bytes(8, "big"))
            for index, item in enumerate(value):
                mac.update(visit(item, path + "/" + str(index), depth + 1))
        elif isinstance(value, _Number):
            kind = "number"
            mac.update(b"Q" + _frame(value.encode("ascii")))
        elif isinstance(value, str):
            kind = "string"
            mac.update(b"S" + _frame(value.encode("utf-8", errors="strict")))
        elif value is None:
            kind = "null"
            mac.update(b"N")
        elif type(value) is bool:
            kind = "boolean"
            mac.update(b"B1" if value else b"B0")
        else:
            raise _Rejected("unexpected_type")
        result = mac.digest()
        path_id = _digest(key, b"path", path.encode("utf-8")).hex()
        nodes[path_id] = {
            "type": kind, "children": children, "digest": result.hex(), **extra
        }
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object,
            parse_int=_Number,
            parse_float=_Number,
            parse_constant=_nonfinite,
        )
        root = visit(value, "", 0)
        return {
            "status": "comparable",
            "algorithm": ALGORITHM,
            "node_count": count,
            "root": root.hex(),
            "nodes": nodes,
        }
    except _Rejected as exc:
        return rejected(str(exc))
    except UnicodeDecodeError:
        return rejected("invalid_utf8")
    except UnicodeEncodeError:
        return rejected("invalid_unicode")
    except json.JSONDecodeError:
        return rejected("invalid_json")
    except RecursionError:
        return rejected("depth_limit")
    except MemoryError:
        return rejected("resource_limit")
    except Exception:
        return rejected("observation_error")
