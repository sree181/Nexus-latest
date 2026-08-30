# Auth0 setup for a live Nexus client demonstration

This is the identity path for a **live operational** Railway demo. Do not enable review authentication on Railway. Review mode is local-only and is labeled `LOCAL REVIEW DATA`.

The command center will not invent incidents or recommendations. After sign-in and the first successful public-feed ingest you should see:

- `LIVE OPERATIONS`
- Active event `Auburn Mobility Operations`
- City of Auburn road closures as a connected public source with published restriction records
- One evidence-bound incident and `awaiting_approval` recommendation derived from those records
- Tiger Transit ETA Spot connected only after `ETA_SPOT_PRODUCTION_APPROVED=true` or `NEXUS_ENABLE_PUBLIC_FEEDS=true` is set on `nexus-api` and `nexus-worker`
- Parking, emergency-access, and TomTom remaining not connected until partner credentials exist

## 1. Create the Auth0 tenant

1. Open [https://auth0.com/signup](https://auth0.com/signup) and create a free account.
2. Create a tenant. Region can be US.
3. Copy the tenant domain, for example `your-tenant.us.auth0.com`.

## 2. Create the API

1. **Applications → APIs → Create API**
2. Name: `Nexus Coordinate API`
3. Identifier: `https://nexus.auburn.edu/api`
4. Signing algorithm: `RS256`
5. Create

This identifier is the Railway `NEXUS_OIDC_AUDIENCE` value. It must match exactly.

## 3. Create the single-page application

1. **Applications → Applications → Create Application**
2. Name: `Nexus Command Center`
3. Type: **Single Page Web Applications**
4. Create
5. Settings:

| Field | Value |
|---|---|
| Allowed Callback URLs | your Railway URL, for example `https://nexus-api-production.up.railway.app` |
| Allowed Logout URLs | the same URL |
| Allowed Web Origins | the same URL |
| Token Endpoint Authentication Method | `None` |

Copy the **Client ID**. That is `NEXUS_OIDC_CLIENT_ID`.

## 4. Create the named operator

1. **User Management → Users → Create User**
2. Email: the operator you will sign in with during the client meeting
3. Password: a password only you and the operator know
4. Open the user → **Metadata** → **app_metadata** and paste:

```json
{
  "nexus_principal_id": "11111111-1111-4111-8111-111111111111",
  "nexus_agency_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "nexus_agency_name": "Auburn Event Mobility Command",
  "nexus_roles": ["event_mobility_lead", "traffic_approver"],
  "nexus_scopes": [
    "event:read",
    "event:manage",
    "incident:read",
    "recommendation:read",
    "recommendation:approve",
    "recommendation:reject",
    "recommendation:request_revision",
    "recommendation:delegate",
    "recommendation:escalate",
    "commitment:read",
    "commitment:transition",
    "audit:read",
    "connector:read",
    "connector:run",
    "graph:read",
    "graph:ingest"
  ],
  "nexus_modes": ["live"]
}
```

Those IDs match the live command-window seed in `004_live_command_window.sql`. `event:manage` lets the command lead open and close operating windows; omit it for operators who should only decide within an existing window.

## 5. Add the access-token Action

1. **Actions → Library → Build Custom → Login / Post Login**
2. Name: `Nexus access claims`
3. Replace the function with:

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://nexus.auburn.edu';
  const meta = event.user.app_metadata || {};
  api.accessToken.setCustomClaim(`${namespace}/nexus_principal_id`, meta.nexus_principal_id);
  api.accessToken.setCustomClaim(`${namespace}/nexus_agency_id`, meta.nexus_agency_id);
  api.accessToken.setCustomClaim(`${namespace}/nexus_agency_name`, meta.nexus_agency_name);
  api.accessToken.setCustomClaim(`${namespace}/nexus_roles`, meta.nexus_roles || []);
  api.accessToken.setCustomClaim(`${namespace}/nexus_scopes`, meta.nexus_scopes || []);
  api.accessToken.setCustomClaim(`${namespace}/nexus_modes`, meta.nexus_modes || ['live']);
  api.accessToken.setCustomClaim(`${namespace}/name`, event.user.name || event.user.email);
};
```

4. Deploy
5. **Actions → Triggers → post-login** and add this action to the flow

## 6. Railway variables on `nexus-api`

Replace the tenant domain and Client ID.

| Variable | Value |
|---|---|
| `NEXUS_AUTH_MODE` | `oidc` |
| `NEXUS_OIDC_ISSUER` | `https://your-tenant.us.auth0.com/` |
| `NEXUS_OIDC_AUDIENCE` | `https://nexus.auburn.edu/api` |
| `NEXUS_OIDC_JWKS_URI` | `https://your-tenant.us.auth0.com/.well-known/jwks.json` |
| `NEXUS_OIDC_CLIENT_ID` | Auth0 application Client ID |
| `NEXUS_ALLOWED_ORIGINS` | `https://<your-api-domain>` |

Redeploy `nexus-api` after saving. The worker does not need OIDC variables.

## 7. Client-demo script

1. Open the Railway public URL.
2. Click **Sign in to live command**.
3. Sign in as the named operator.
4. Confirm the header shows `LIVE OPERATIONS`, not `LOCAL REVIEW DATA`.
5. Confirm City of Auburn closures is connected and shows recent observations, not `No observations`.
6. Point at the **Window** field in the header. Explain that the pack decides which feeds are read, which agent desks are staffed, and which detection rules may open an incident.
7. Open a pending recommendation. Point at the agent-desk chips: which desks contributed, which stayed silent because they had no feed, and any named dissent. Then cite the upstream record and **Review & approve**. Approval creates the commitments the rule's playbook assigns.
8. If the decision queue is empty, say so plainly: no authoritative record crossed a detection rule in the current window. Nexus does not manufacture an incident to fill the screen.
9. Explain that parking occupancy, emergency-access, and TomTom stay gated until real partner credentials exist. Do not invent those feeds.
