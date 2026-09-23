"""
connectors/mde.py — Microsoft Defender for Endpoint connector.

Authentication
--------------
Uses OAuth 2.0 client_credentials flow against Azure Active Directory.
Required credentials (set in ConnectorConfig.credentials):
  tenant_id     : Azure AD tenant GUID
  client_id     : App registration client ID
  client_secret : App registration client secret

Required Azure AD app permissions (application, not delegated):
  AdvancedQuery.Read.All          (for custom KQL queries)
  Alert.Read.All                  (for /api/alerts)
  Machine.Read.All                (for /api/machines)
  Ti.ReadWrite.All                (optional, for threat indicators)

Endpoints pulled
----------------
  alerts    GET /api/alerts?$top=N&$orderby=lastUpdateTime+desc
  machines  GET /api/machines?$top=N
  events    GET /api/deviceevents?$top=N   (Advanced Hunting equivalent)

Hyperedge mapping
-----------------
  Alert:
    members = [machineId, relatedUser.accountName, file.sha1, ip]
    ts      = firstEventTime (Unix ms)
    props   = {alertId, title, severity, category, status}

  Machine:
    members = [machineId, lastIpAddress, osPlatform]
    ts      = lastSeen
    props   = {machineName, riskScore, exposureLevel, healthStatus}
"""

from __future__ import annotations

import time
import urllib.request
import urllib.parse
import urllib.error
import json
from typing import Any

from hypermeshdb.connectors.base import ConnectorBase, ConnectorConfig, SyncResult

# MDE API base URL
_MDE_BASE = "https://api.securitycenter.microsoft.com"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SCOPE     = "https://api.securitycenter.microsoft.com/.default"


def _http_post(url: str, data: dict) -> dict:
    body    = urllib.parse.urlencode(data).encode("utf-8")
    req     = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ts_to_unix(ts_str: str | None) -> int:
    """Parse ISO-8601 or MDE timestamp string to Unix seconds."""
    if not ts_str:
        return int(time.time())
    try:
        from datetime import datetime, timezone
        # MDE uses "2024-01-15T12:34:56.000Z" format
        ts_str = ts_str.rstrip("Z").split(".")[0]
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return int(time.time())


class MDEConnector(ConnectorBase):
    """
    Microsoft Defender for Endpoint connector.

    Pulls security alerts and machine inventory via the MDE REST API.
    """

    # ── Auth ──────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        creds      = self._cfg.credentials
        tenant_id  = creds.get("tenant_id", "")
        client_id  = creds.get("client_id", "")
        client_sec = creds.get("client_secret", "")

        if not all([tenant_id, client_id, client_sec]):
            raise ValueError("MDE connector requires tenant_id, client_id, and client_secret")

        url  = _TOKEN_URL.format(tenant_id=tenant_id)
        data = {
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_sec,
            "scope":         _SCOPE,
        }
        resp  = _http_post(url, data)
        token = resp.get("access_token")
        if not token:
            err = resp.get("error_description", resp.get("error", "Unknown error"))
            raise RuntimeError(f"MDE OAuth2 failed: {err}")
        return token

    # ── Fetchers ──────────────────────────────────────────────────────────

    def _fetch_alerts(self, token: str, limit: int) -> list[dict]:
        url = (
            f"{_MDE_BASE}/api/alerts"
            f"?$top={min(limit, 10000)}"
            f"&$orderby=lastUpdateTime+desc"
            f"&$select=id,title,severity,category,status,machineId,"
            f"relatedUser,fileStates,networkConnections,firstEventTime,lastEventTime"
        )
        resp = _http_get(url, token)
        return resp.get("value", [])

    def _fetch_machines(self, token: str, limit: int) -> list[dict]:
        url = (
            f"{_MDE_BASE}/api/machines"
            f"?$top={min(limit, 10000)}"
            f"&$orderby=lastSeen+desc"
            f"&$select=id,computerDnsName,lastIpAddress,lastSeen,"
            f"osPlatform,riskScore,exposureLevel,healthStatus"
        )
        resp = _http_get(url, token)
        return resp.get("value", [])

    # ── Entity registry helper ────────────────────────────────────────────

    @staticmethod
    def _resolve_entity(
        em: dict[str, int],        # value -> id mapping
        id_to_meta: dict[int, dict],
        value: str,
        etype: str,
    ) -> int:
        if value not in em:
            nid = len(em) + 1
            em[value] = nid
            short = value[:30] + "…" if len(value) > 30 else value
            id_to_meta[nid] = {"type": etype, "raw": value, "display": value, "short": short}
        return em[value]

    # ── Hyperedge mappers ─────────────────────────────────────────────────

    def _alert_to_hyperedge(
        self, alert: dict, em: dict, id_to_meta: dict
    ) -> dict | None:
        members = []

        resolve = lambda v, t: self._resolve_entity(em, id_to_meta, v, t)

        # Machine
        if mid := alert.get("machineId"):
            members.append(resolve(mid, "machine"))

        # Related user
        ru = alert.get("relatedUser") or {}
        if acct := ru.get("accountName"):
            members.append(resolve(acct, "account"))

        # File hashes
        for fs in (alert.get("fileStates") or []):
            sha = (fs.get("fileDetails") or {}).get("sha1")
            if sha:
                members.append(resolve(sha, "process"))  # treat as process artifact

        # Network connections
        for nc in (alert.get("networkConnections") or []):
            ip = nc.get("remoteIpAddress") or nc.get("localIpAddress")
            if ip:
                members.append(resolve(ip, "ip"))

        if not members:
            return None

        return {
            "members":   members,
            "event_ts":  _ts_to_unix(alert.get("firstEventTime")),
            "weight":    1.0,
            "props": {
                "alert_id": alert.get("id", ""),
                "title":    alert.get("title", ""),
                "severity": alert.get("severity", ""),
                "category": alert.get("category", ""),
                "status":   alert.get("status", ""),
            },
        }

    def _machine_to_hyperedge(
        self, machine: dict, em: dict, id_to_meta: dict
    ) -> dict | None:
        members = []
        resolve = lambda v, t: self._resolve_entity(em, id_to_meta, v, t)

        mid = machine.get("id")
        if mid:
            name = machine.get("computerDnsName", mid)
            em[mid]          = em.get(mid, len(em) + 1)
            id_to_meta[em[mid]] = {"type": "machine", "raw": mid, "display": name, "short": name[:30]}
            members.append(em[mid])

        if ip := machine.get("lastIpAddress"):
            members.append(resolve(ip, "ip"))

        if not members:
            return None

        return {
            "members":   members,
            "event_ts":  _ts_to_unix(machine.get("lastSeen")),
            "weight":    1.0,
            "props": {
                "machine_name":    machine.get("computerDnsName", ""),
                "os_platform":     machine.get("osPlatform", ""),
                "risk_score":      machine.get("riskScore", ""),
                "exposure_level":  machine.get("exposureLevel", ""),
                "health_status":   machine.get("healthStatus", ""),
            },
        }

    # ── ConnectorBase interface ───────────────────────────────────────────

    def test_connection(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            token = self._get_token()
            # Lightweight call to verify scope
            url   = f"{_MDE_BASE}/api/alerts?$top=1&$select=id"
            _http_get(url, token)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {"ok": True, "message": "Connected to MDE API successfully", "latency_ms": ms}
        except Exception as exc:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            return {"ok": False, "message": str(exc), "latency_ms": ms}

    def preview_schema(self, sample_n: int = 5) -> dict[str, Any]:
        try:
            token  = self._get_token()
            alerts = self._fetch_alerts(token, limit=sample_n)
            if not alerts:
                return {"columns": [], "sample_rows": [], "total_est": 0}
            columns = sorted({k for row in alerts for k in row})
            return {"columns": columns, "sample_rows": alerts[:5], "total_est": None}
        except Exception as exc:
            return {"columns": [], "sample_rows": [], "total_est": None, "error": str(exc)}

    def sync(self, limit: int = 10_000) -> SyncResult:
        """Stream MDE alerts/machines through the hypermesh_ingest pipeline."""
        from hypermeshdb.connectors.adapters import MDESourceAdapter
        from hypermeshdb.connectors.spec_bridge import connector_spec

        spec = connector_spec(self._cfg)
        adapter = MDESourceAdapter(
            credentials=self._cfg.credentials,
            settings=self._cfg.settings,
            spec=spec,
            target_table=(self._cfg.target_table or "MDE_ALERTS").upper(),
            since=self.read_cursor(),
            limit=limit,
        )
        return self.run_engine_sync(adapter, mode="append")
