"""Jinja helpers for render templates. Pure functions, safe in the sandbox.

Filters:
  netmask    '10.1.0.0/16'   -> '10.1.0.0 255.255.0.0'   (FortiOS, IOS routes)
  wildcard   '10.1.0.0/16'   -> '10.1.0.0 0.0.255.255'   ('any', /32 -> 'host A')
  cidr       'host 10.0.0.1' -> '10.0.0.1/32'            ('any' stays 'any')
  ip         'host 10.0.0.1' -> '10.0.0.1'               (address part only)
  safe_name  'a/b c'         -> 'a_b_c'                  (object names, max 63)
  quote      'say "hi"'      -> '"say \\"hi\\""'          (CLI string)

Global:
  required(value, 'field')   fails the render (fail closed) when value is empty
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from jinja2 import TemplateError


class TemplateRequirementError(TemplateError):
    """A template declared a field as required and the intent lacks it."""


def _net(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    v = str(value).strip().removeprefix("host ").strip()
    return ipaddress.ip_network(v, strict=False)


def netmask(value: Any) -> str:
    n = _net(value)
    return f"{n.network_address} {n.netmask}"


def wildcard(value: Any) -> str:
    if str(value).strip().lower() == "any":
        return "any"
    n = _net(value)
    if n.prefixlen == n.max_prefixlen:
        return f"host {n.network_address}"
    return f"{n.network_address} {n.hostmask}"


def cidr(value: Any) -> str:
    if str(value).strip().lower() == "any":
        return "any"
    return str(_net(value))


def ip(value: Any) -> str:
    return str(_net(value).network_address)


def safe_name(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:63]


def quote(value: Any) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def required(value: Any, what: str) -> Any:
    if value is None or value == "" or value == []:
        raise TemplateRequirementError(f"this platform needs {what} in the intent")
    return value


FILTERS = {
    "netmask": netmask,
    "wildcard": wildcard,
    "cidr": cidr,
    "ip": ip,
    "safe_name": safe_name,
    "quote": quote,
}
GLOBALS = {"required": required}
