"""
Microsoft Defender for Endpoint source adapter.

Authenticates (OAuth2 client_credentials), pulls alerts (and optionally
machines), and yields one hyperedge per alert/machine whose members are the
machine / user / file-hash / IP entities.  Only alerts newer than ``since``
(by ``lastUpdateTime``) are streamed; the newest timestamp is exposed as
``cursor``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

from hypermesh_ingest.adapters.base import BaseAdapter, IRRecord
from hypermesh_ingest.ir.records import HyperedgeMember, HyperedgeRecord
from hypermesh_ingest.spec.schema import MappingSpec

from ._records import _prov, make_vertex

_MDE_BASE = "https://api.securitycenter.microsoft.com"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SCOPE = "https://api.securitycenter.microsoft.com/.default"


def _http_post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ts_to_unix(ts_str: Optional[str]) -> int:
    """Parse an MDE ISO-8601 timestamp to Unix seconds."""
    if not ts_str:
        return int(time.time())
    try:
        from datetime import datetime, timezone
        clean = ts_str.rstrip("Z").split(".")[0]
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return int(time.time())


class MDESourceAdapter(BaseAdapter):
    def __init__(
        self,
        *,
        credentials: dict,
        settings: dict,
        spec: MappingSpec,
        target_table: str,
        since: Optional[float] = None,
        limit: int = 10_000,
    ) -> None:
        super().__init__(spec)
        self._creds = credentials
        self._settings = settings
        self._target = target_table
        self._since = since or 0.0
        self._limit = limit
        self.cursor: float = self._since

    @property
    def source_id(self) -> str:
        return f"mde://{self._creds.get('tenant_id', '')}"

    # ── auth + fetch (ported from MDEConnector) ─────────────────────────────

    def get_token(self) -> str:
        c = self._creds
        if not all([c.get("tenant_id"), c.get("client_id"), c.get("client_secret")]):
            raise ValueError("MDE requires tenant_id, client_id, client_secret")
        resp = _http_post(
            _TOKEN_URL.format(tenant_id=c["tenant_id"]),
            {
                "grant_type": "client_credentials",
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
                "scope": _SCOPE,
            },
        )
        token = resp.get("access_token")
        if not token:
            raise RuntimeError(f"MDE OAuth2 failed: {resp.get('error_description', resp.get('error'))}")
        return token

    def _fetch_alerts(self, token: str, limit: int) -> list[dict]:
        url = (
            f"{_MDE_BASE}/api/alerts?$top={min(limit, 10000)}"
            f"&$orderby=lastUpdateTime+desc"
            f"&$select=id,title,severity,category,status,machineId,relatedUser,"
            f"fileStates,networkConnections,firstEventTime,lastUpdateTime"
        )
        return _http_get(url, token).get("value", [])

    def _fetch_machines(self, token: str, limit: int) -> list[dict]:
        url = (
            f"{_MDE_BASE}/api/machines?$top={min(limit, 10000)}"
            f"&$orderby=lastSeen+desc"
            f"&$select=id,computerDnsName,lastIpAddress,lastSeen,osPlatform"
        )
        return _http_get(url, token).get("value", [])

    # ── IR mappers ──────────────────────────────────────────────────────────

    def _alert_ir(self, alert: dict) -> Iterator[IRRecord]:
        members: list[HyperedgeMember] = []
        for value, etype in self._alert_entities(alert):
            local_id = f"{etype}:{value}"
            yield make_vertex(local_id, etype=etype, display=value,
                              source_id=self.source_id, spec=self.spec)
            members.append(HyperedgeMember.make(local_id))
        if len(members) < 2:
            return
        ts = ts_to_unix(alert.get("firstEventTime"))
        yield HyperedgeRecord.make(
            local_id=f"{self._target}_alert_{alert.get('id', '')}",
            table=self._target,
            type=self._target,
            members=members,
            provenance=_prov(self.source_id, self.spec, alert_id=alert.get("id", "")),
            properties={"weight": 1.0},
            valid_time=(ts, None) if ts else None,
        )

    @staticmethod
    def _alert_entities(alert: dict) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if mid := alert.get("machineId"):
            out.append((str(mid), "machine"))
        if acct := (alert.get("relatedUser") or {}).get("accountName"):
            out.append((str(acct), "account"))
        for fs in alert.get("fileStates") or []:
            sha = (fs.get("fileDetails") or {}).get("sha1")
            if sha:
                out.append((str(sha), "process"))
        for nc in alert.get("networkConnections") or []:
            ip = nc.get("remoteIpAddress") or nc.get("localIpAddress")
            if ip:
                out.append((str(ip), "ip"))
        return out

    def _machine_ir(self, machine: dict) -> Iterator[IRRecord]:
        members: list[HyperedgeMember] = []
        mid = machine.get("id")
        if mid:
            local_id = f"machine:{mid}"
            yield make_vertex(local_id, etype="machine",
                              display=machine.get("computerDnsName", str(mid)),
                              source_id=self.source_id, spec=self.spec)
            members.append(HyperedgeMember.make(local_id))
        if ip := machine.get("lastIpAddress"):
            local_id = f"ip:{ip}"
            yield make_vertex(local_id, etype="ip", display=str(ip),
                              source_id=self.source_id, spec=self.spec)
            members.append(HyperedgeMember.make(local_id))
        if len(members) < 2:
            return
        ts = ts_to_unix(machine.get("lastSeen"))
        yield HyperedgeRecord.make(
            local_id=f"{self._target}_machine_{mid}",
            table=self._target,
            type=self._target,
            members=members,
            provenance=_prov(self.source_id, self.spec, machine_id=str(mid)),
            properties={"weight": 1.0},
            valid_time=(ts, None) if ts else None,
        )

    # ── stream ───────────────────────────────────────────────────────────────

    def stream(self) -> Iterator[IRRecord]:
        token = self.get_token()
        if self._settings.get("sync_alerts", True):
            for alert in self._fetch_alerts(token, self._limit):
                upd = ts_to_unix(alert.get("lastUpdateTime"))
                if upd <= self._since:
                    continue
                self.cursor = max(self.cursor, float(upd))
                yield from self._alert_ir(alert)
        if self._settings.get("sync_machines", False):
            for machine in self._fetch_machines(token, min(self._limit, 2000)):
                seen = ts_to_unix(machine.get("lastSeen"))
                if seen <= self._since:
                    continue
                self.cursor = max(self.cursor, float(seen))
                yield from self._machine_ir(machine)
