# `blizzard_mock.idp` — stub OAuth identity provider

## Contract

A standalone HTTP service that stands in for a **real OAuth/OIDC provider** so the
hub's `hub/auth/oauth/` provider seam can be service/e2e-tested against a
real HTTP counterpart — no tokens, no network beyond this one local process. It serves
**both shapes the seam supports at one origin**:

- **OIDC** — discovery (`/.well-known/openid-configuration`), authorize, token (a real
  RS256-signed `id_token`, verifiable against the served JWKS).
- **GitHub-style** — `/login/oauth/authorize`, `/login/oauth/access_token`, `GET /user`,
  `GET /user/emails`.

Unlike a real provider it shows **no login UI**: `authorize` redirects back
immediately. With `login_hint`, either authorize endpoint chooses the identity
registered under that primary email (case-insensitively); an unknown hint
returns `error=access_denied`. Without a hint, the code binds the current
`/_levers/profile` profile as before. A test can still flip that profile between
authorize calls to script a handle rename without a browser.

**Levered**, mirroring the forge/mock-hub's own control surface:

| Lever | Effect |
|-------|--------|
| `PUT /_levers/profile` | The identity (`subject`, `handle`, `email`, `email_verified`, `secondary_emails`) the *next* completed dance resolves to — also how a test drives an unverified email or a handle rename. `secondary_emails` lists the account's other addresses, each `{"email", "verified"}`; `GET /user/emails` answers the primary first and then each of them with its own `verified` flag, while the OIDC `id_token` carries the primary alone, as a real one carries one address. An address listed twice (case-insensitively, the primary included) is refused with 422. The optional `role` field rides along for a consuming hub to read and apply at first-login time; it is additive only — it never surfaces in the signed `id_token` or the GitHub-shaped `/user` response, which stay provider-shaped |
| `POST /_levers/identities` | Register a `Profile` under its primary `email`, case-insensitively; registering the same email replaces it, and a missing primary email returns 422. Answers `{"identity": <profile>}` |
| `GET /_levers/identities` | List registered profiles in registration order as `{"identities": [<profile>, …]}` |
| `PUT /_levers/refuse_callback` | Armed, `authorize` redirects back with `error=access_denied` instead of a code |
| `POST /_levers/reset` | Back to the default profile, lever cleared, registered identities and codes/tokens forgotten |

## Tenants and projects

The IdP holds no tenant or project: it reports who signed in, and the hub decides what that
person may reach. A tenant invitation is accepted only by a sign-in reporting a verified
address equal to the invited one, and the GitHub shape reports every address
`GET /user/emails` lists. A test proves a secondary-address acceptance by listing the invited
address in `secondary_emails` with `verified: true`, and a refusal by listing it unverified.
An OIDC sign-in carries the primary alone, so only the primary can match there.

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

In-memory only, one process-wide default `Profile`, an email-keyed identity map,
and a fresh RS256 keypair minted at startup — no persistence. Codes and tokens
retain the profile chosen at authorization. `blizzard-mock-idp` is restarted
between scenario runs, never mid-run.

## Standing instance — a running hub, a real browser, outside a test's lifetime

The e2e tier (`tests/e2e/test_login_session_e2e.py` in `blizzard`) only proves the OAuth
dance for a pytest fixture's lifetime. To point a **standing** hub — one left running for
a human or an agent to click through by hand — at this IdP instead:

```bash
# 1. The IdP itself, on any free port
uv run blizzard-mock-idp --host 127.0.0.1 --port <idp-port>

# 2. A hub runtime dir — scratch, or any dir that is not $BZ_HUB_RUNTIME of a live
#    per-env service you don't want to disturb
BZ_OAUTH_SECRET=some-secret uv run blizzard-hub init <hub-dir>
```

Then edit `<hub-dir>/blizzard-hub.toml`'s `[auth]` table (`hub/config.py`'s
`AUTH_MODE_OAUTH` — note the mode is `"oauth"`, not `"oidc"`; `oidc` is the *provider*
`type` below) to:

```toml
[auth]
mode = "oauth"

[[auth.oauth.provider]]
name = "oidc-standing"
type = "oidc"
display_name = "Standing Stub SSO"
client_id = "cid"
client_secret_env = "BZ_OAUTH_SECRET"
issuer = "http://127.0.0.1:<idp-port>"
```

```bash
# 3. Serve the built board and point a browser at it
BZ_OAUTH_SECRET=some-secret uv run blizzard-hub host --dir <hub-dir> --host 127.0.0.1 --port <hub-port>
```

A browser hitting `http://127.0.0.1:<hub-port>/` reaches the `/login` gate with a
`Standing Stub SSO` button; clicking it runs the real dance against this process and
lands on the hub authenticated. `PUT /_levers/profile` before a login scripts which
identity that dance resolves to — flip it between logins (fresh browser context, no
cookie carried over) to drive distinct scripted identities without a UI. A role is set
directly in `<hub-dir>/data/hub.db`'s `users` table (the same seam ahead of a
role-assignment API) — reload the browser (same session cookie) to see the board render
under the new role.
