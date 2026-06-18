import copy
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Limit:
    requests: int
    window_seconds: int


@dataclass(frozen=True)
class ResolvedLimits:
    org_id: str
    tier: str
    org: Limit
    endpoint: Limit
    endpoint_name: str


class RateLimitConfig:
    """Loads rate limits from JSON and hot-reloads when the file changes."""

    def __init__(self, path: str):
        self.path = path
        self._mtime: Optional[float] = None
        self._data: Dict = {}
        self.reload_if_needed(force=True)

    def reload_if_needed(self, force: bool = False) -> None:
        mtime = os.path.getmtime(self.path)
        if not force and self._mtime == mtime:
            return

        with open(self.path, "r", encoding="utf-8") as config_file:
            data = json.load(config_file)

        self._validate(data)
        self._data = data
        self._mtime = mtime

    def resolve(self, org_id: str, method: str, path: str) -> ResolvedLimits:
        self.reload_if_needed()

        org_entry = self._data.get("orgs", {}).get(org_id, {})
        tier_name = org_entry.get("tier", self._data["default_tier"])
        tier = copy.deepcopy(self._data["tiers"][tier_name])
        overrides = org_entry.get("overrides", {})

        for limit_name, limit_value in overrides.items():
            tier[limit_name] = limit_value

        endpoint_name = "write" if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} else "read"
        endpoint_limit = tier[endpoint_name]

        for override in self._data.get("endpoint_overrides", []):
            if override["method"].upper() == method.upper() and override["path"] == path:
                endpoint_name = f"{method.upper()} {path}"
                endpoint_limit = override["limit"]
                break

        return ResolvedLimits(
            org_id=org_id,
            tier=tier_name,
            org=self._limit_from_dict(tier["org"]),
            endpoint=self._limit_from_dict(endpoint_limit),
            endpoint_name=endpoint_name,
        )

    def _limit_from_dict(self, value: Dict) -> Limit:
        return Limit(requests=int(value["requests"]), window_seconds=int(value["window_seconds"]))

    def _validate(self, data: Dict) -> None:
        if "default_tier" not in data or data["default_tier"] not in data.get("tiers", {}):
            raise ValueError("config must define default_tier and include it in tiers")

        for tier_name, tier in data.get("tiers", {}).items():
            for limit_name in ("org", "read", "write"):
                if limit_name not in tier:
                    raise ValueError(f"tier {tier_name} is missing {limit_name} limit")
                self._validate_limit(tier[limit_name], f"tiers.{tier_name}.{limit_name}")

        for org_id, org in data.get("orgs", {}).items():
            tier_name = org.get("tier", data["default_tier"])
            if tier_name not in data["tiers"]:
                raise ValueError(f"org {org_id} references unknown tier {tier_name}")
            for limit_name, limit in org.get("overrides", {}).items():
                if limit_name not in {"org", "read", "write"}:
                    raise ValueError(f"org {org_id} has unknown override {limit_name}")
                self._validate_limit(limit, f"orgs.{org_id}.overrides.{limit_name}")

        for index, override in enumerate(data.get("endpoint_overrides", [])):
            for field in ("method", "path", "limit"):
                if field not in override:
                    raise ValueError(f"endpoint_overrides[{index}] is missing {field}")
            self._validate_limit(override["limit"], f"endpoint_overrides[{index}].limit")

    def _validate_limit(self, limit: Dict, location: str) -> None:
        if int(limit.get("requests", 0)) <= 0:
            raise ValueError(f"{location}.requests must be positive")
        if int(limit.get("window_seconds", 0)) <= 0:
            raise ValueError(f"{location}.window_seconds must be positive")
