# `blizzard_mock.idp` — stub OAuth identity provider

## Contract

A standalone HTTP service that stands in for a **real OAuth/OIDC provider** so the
hub's `hub/auth/oauth/` provider seam (issue #92) can be service/e2e-tested against a
real HTTP counterpart — no tokens, no network beyond this one local process. It serves
**both shapes the seam supports at one origin**:

- **OIDC** — discovery (`/.well-known/openid-configuration`), authorize, token (a real
  RS256-signed `id_token`, verifiable against the served JWKS).
- **GitHub-style** — `/login/oauth/authorize`, `/login/oauth/access_token`, `GET /user`,
  `GET /user/emails`.

Unlike a real provider it shows **no login UI**: `authorize` redirects back
immediately with a code for whichever `Profile` the `/_levers/profile` control
currently holds. A test flips the profile between two authorize calls to script two
distinct identities (or a handle rename on the same `subject`) with no browser
involved.

**Levered**, mirroring the forge/mock-hub's own control surface:

| Lever | Effect |
|-------|--------|
| `PUT /_levers/profile` | The identity (`subject`, `handle`, `email`, `email_verified`) the *next* completed dance resolves to — also how a test drives an unverified email or a handle rename |
| `PUT /_levers/refuse_callback` | Armed, `authorize` redirects back with `error=access_denied` instead of a code |
| `POST /_levers/reset` | Back to the default profile, lever cleared, codes/tokens forgotten |

## Binary & configuration

`blizzard-mock-idp` → `blizzard_mock.idp.cli:main` (a click command). Serves the
FastAPI app via uvicorn.

| Setting | Flag | Env | Default |
|---------|------|-----|---------|
| Bind host | `--host` | `BZ_IDP_HOST` | `127.0.0.1` |
| Bind port | `--port` | `BZ_IDP_PORT` | `8090` |

A hub pointed at this process sets `[[auth.oauth.provider]] issuer` (`oidc`) or
`api_base` (`github`) to `http://{host}:{port}` — the `github` conformer's `web_base`/
`api_base` both collapse to that one origin, matching how this stub serves both OAuth2
endpoint families together.

## State

In-memory only, one process-wide `Profile` + a fresh RS256 keypair minted at startup —
no persistence. `blizzard-mock-idp` is restarted between scenario runs, never mid-run.
