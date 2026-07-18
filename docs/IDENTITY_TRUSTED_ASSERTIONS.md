# Identity And Trusted App Assertions

This note captures the intended direction for Agent Smith identity resolution.
It complements [Parent App Integration](PARENT_APP_INTEGRATION.md), which shows
the concrete HTTP integration shape.

## Direction

Smith should not be the primary login/password system for every product that
uses it. Parent applications, identity brokers, or dedicated auth services own
human authentication. Smith owns:

- canonical `principal_id` for runtime state;
- the mapping from trusted external identities to principals;
- authorization, policy, context filtering, and audit for agent usage.

The contract is:

```text
External system authenticates the human user.
External system sends Smith its provider API key.
External system also sends a signed, short-lived actor assertion.
Smith resolves the provider from the API key.
Smith verifies the assertion and resolves an internal principal.
Smith authorizes agent runtime actions using that principal.
```

This keeps Smith decoupled from login UX while still giving Smith a stable
identity model for sessions, memories, tools, permissions, and audit logs.

## Assertion Shape

A trusted app assertion is a compact signed token, currently implemented as
HS256 JWS in `src/agent_smith/app/auth.py`.

The payload should include:

```json
{
  "iss": "adw",
  "aud": "agent-smith",
  "sub": "adw-user-uuid",
  "jti": "unique-request-id",
  "iat": 1783420000,
  "exp": 1783420300,
  "actor": {
    "displayName": "Nguyen Van A",
    "email": "a@company.vn",
    "roles": ["manager"],
    "department": "IT",
    "upstreamAuth": {
      "provider": "company-sso",
      "assurance": "password+mfa",
      "authTime": 1783419900,
      "method": "oidc"
    }
  }
}
```

Claim meanings:

```text
iss = issuer
  The trusted app or auth service that signs the assertion.
  Example: "adw", "smith-auth", "finance-app".

aud = audience
  The intended recipient of the assertion.
  For Smith this should be a configured Smith audience such as "agent-smith".

sub = subject
  The external user identifier inside the API-key-resolved provider namespace.
  Smith maps identity_provider_id + sub to an internal principal_id.

jti = JWT ID
  A unique token/request id. Smith stores it until expiry to reject replay.

iat = issued at
  Unix timestamp, in seconds, when the assertion was created.

exp = expires at
  Unix timestamp, in seconds, when the assertion expires.
  Keep this short; the current verifier limits assertion lifetime to 300s.

actor = normalized actor profile
  Public actor metadata that can enrich context/provenance.
  It must not include provider or subject; provider comes from the API key and
  subject comes from `sub`.
```

`upstreamAuth` is provenance/context only. It can describe how the parent app
authenticated the user, but it should not automatically create another external
identity mapping.

## API Key Versus Signed Assertion

An API key can authenticate the calling partner or service, but it should not be
the whole user identity contract.

With only an API key, Smith can know:

```text
this request came from partner A
```

But the user payload is still just data inside the request body unless it is
bound to the authenticated caller and protected against tampering/replay.

With a signed assertion, Smith can additionally verify:

```text
partner A signed these exact claims;
the claims were meant for Smith;
the token is fresh;
this token has not already been used;
sub is the per-request actor subject inside partner A's namespace.
```

A good production shape can use both:

```text
API key / mTLS / service auth
  -> authenticates the calling backend connection
  -> resolves identity_provider_id

signed short-lived assertion
  -> authenticates the per-request actor claims
```

The API key identifies the integration. The assertion identifies the actor for a
specific invocation.

## Provider Control Plane

Provider records and credentials are managed through the standalone Admin HTTP process.
These APIs require an admin session cookie, exact Origin and CSRF header for mutations.

```text
Cookie: <admin-session-cookie>
X-CSRF-Token: <csrf-cookie-value>
```

The HTTP transport is a FastAPI app. Run it locally with:

```bash
poetry run uvicorn agent_smith.transports.admin_http.main:app --host 127.0.0.1 --port 8766
```

The first version is intentionally admin-created:

```text
POST /api/identity-providers
POST /api/identity-providers/{providerId}/api-keys
POST /api/identity-providers/{providerId}/assertion-keys
```

Smith generates both credentials:

- Provider API key: raw value returned once, stored as one-way hash plus prefix.
- Assertion signing secret: raw value returned once, stored encrypted at rest.

Runtime invocation then uses both:

```text
X-Agent-Smith-Provider-Key: <raw-api-key>
Authorization: Bearer <signed-short-lived-assertion>
```

## Identity Namespace Rule

Smith must not treat a request-provided `(provider, subject)` pair as globally
safe. The provider namespace is resolved from the provider API key.

Safer identity key:

```text
identity_provider_id + subject
```

Why this matters:

```text
Partner A may send provider="hris", subject="123".
Partner B may also send provider="hris", subject="123".
```

Those may refer to different people unless Smith knows both partners are talking
about the same trusted HRIS namespace. The issuer/provider record is the trust
boundary that makes the namespace meaningful.

Current code already partially enforces this at verification time:

- the provider API key resolves `identity_provider_id`, slug, and issuer;
- assertion `iss` must match the resolved provider issuer;
- signature key is selected from that provider's active assertion keys;
- `sub` becomes the external identity subject for that provider namespace.

The database model should reflect the same boundary:

```text
identity_providers
  id
  issuer
  slug
  status
  metadata

identity_provider_api_keys
  id
  provider_id
  name
  key_hash
  key_prefix
  status
  expires_at
  revoked_at

identity_provider_assertion_keys
  id
  provider_id
  kid
  alg
  encrypted_secret
  encryption_scheme
  status
  expires_at
  revoked_at

external_identities
  id
  principal_id
  identity_provider_id
  subject
  email
  display_name
  metadata
  last_seen_at

unique(identity_provider_id, subject)
```

## Principal Merge Direction

Principal merge should not be built in the first pass, but the model should not
block it.

Target direction:

```text
external identity link first;
canonical principal resolution second;
physical data migration later, per domain.
```

If one human accidentally creates two principals, Smith should eventually be
able to mark one as canonical and one as merged/superseded:

```text
principals
  id
  status: active | inactive | pending | merged
  canonical_principal_id nullable

principal_merge_events
  id
  from_principal_id
  to_principal_id
  reason
  verified_by_identity_id
  created_at
  metadata
```

The risky part is not the identity link itself; it is merging domain data such
as sessions, memory, MCP credentials, approvals, billing, and audit. Each domain
needs its own merge policy. Until that exists, principal merge should be an
explicit, auditable operation, not an automatic side effect of matching email.

## Near-Term Implementation Notes

- Keep Smith core free from product-specific login flows.
- Treat Smith Auth, if built, as just another trusted issuer/provider.
- Require provider API key plus signed short-lived assertion for external invoke.
- Manage providers and credentials through the standalone Admin HTTP endpoints.
- Return raw API keys and assertion secrets only once at creation time.
- Scope external identity uniqueness by provider record, not bare provider name.
- Treat `sub` as the external user id inside the API-key-resolved provider.
- Keep `jti` replay protection and short `exp`.
- Store only public/sanitized actor metadata on `external_identities.metadata`.
- Do not auto-merge principals from email alone.
