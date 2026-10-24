# YouTrack REST API notes

Behaviour of the YouTrack REST API as observed for this integration. Verified against a self-hosted
YouTrack **2026.2 (build 18687)** with a permanent token (the account's role was not varied), read-only requests only.
Fixtures in `tests/fixtures/api/` are anonymised reproductions of the observed shapes.

Items marked **unverified** could not be tested and must be re-checked before relying on them.

## Authentication and base URL

| Topic | Finding |
|---|---|
| Auth | `Authorization: Bearer <permanent token>`; send `Accept: application/json` |
| Invalid token | `401` with JSON `{"error":"Unauthorized","error_description":"Invalid token"}` |
| Valid token, no permission | `403` — **unverified** (the test token had access to everything it asked for) |
| Plain HTTP | `301` redirect to HTTPS; the config flow should normalise to `https://` and not follow redirects blindly with the token |
| Base path | The instance under test serves `/api` at the host root. Installations in a context path (e.g. `/youtrack`) are **unverified**; the flow should accept a URL with path and test `<url>/api/config` |
| Version | `GET /api/config?fields=version,build` returns `{"version":"2026.2","build":"18687"}` and also serves as a cheap connection test |
| Current user | `GET /api/users/me?fields=login,name,guest` |

## Projects

`GET /api/admin/projects?fields=id,name,shortName,archived&$top=100` lists projects. Archived projects are
included (`archived: true`) and should be hidden in the project picker. Use the internal `id` (e.g. `0-7`)
as the stable subentry unique ID; `shortName` can be renamed.

## Counting issues

`POST /api/issuesGetter/count?fields=count` with body `{"query":"project: DEMO #Unresolved"}`.

- **The first call for a new query typically returns `{"count": -1}`** (still computing). A repeat call after
  about one second returned the real value. The client must retry (e.g. up to 3 times with 1 s delay) and
  otherwise report "unknown" instead of `-1`.
- Results are cached server-side afterwards; repeated polls are cheap (~10–50 ms).
- An empty query counts all issues visible to the user.
- Syntax errors usually return `400` with JSON `{"error":"invalid_query","error_field":"query",...}` — usable for
  validating user-defined queries (HAY custom query sensors). Not every malformed query is rejected: a bare `((`
  was accepted by both the count and the issue list endpoint. The options flow must therefore treat the count
  check as best effort.
- Query used for "overdue" depends on a **date custom field** (`Due Date`). The test project has no such field;
  referencing it gives `invalid_query`. The overdue sensor must be created only when the project has a
  due-date field, or made configurable.

## Listing issues

`GET /api/issues?query=<q>&fields=<projection>&$top=<n>&$skip=<m>`

- Projection used (all fields available to a normal user):
  `idReadable,summary,created,updated,resolved,project(shortName),reporter(login),customFields(name,value(name,login,minutes))`.
- Timestamps are epoch milliseconds; `resolved` is `null` for open issues.
- Custom fields come as a list of `{name, value, $type}`; `State` is `StateIssueCustomField`, `Assignee`
  `SingleUserIssueCustomField` (value `null` when unassigned). Field names are project-specific, so look them up
  by name and tolerate absence.
- **Always send `$top`.** Without it the instance returned all 171 issues in a single response.
- `$skip` pagination works (20 / 19 / 0 for three pages over 39 issues).
- Incremental refresh works with `query=project: X updated: 2026-10-24T18:20:00 .. *` (ISO timestamp, no zone
  → server/user time zone; convert carefully or use the activity feed for exact cursors).
- Invalid or unknown issue IDs (`/api/issues/DEMO-99999`) and unknown projects return **`404` with an HTML page,
  not JSON**. Error mapping must check the content type before parsing JSON.

## Activity feed

`GET /api/activities?categories=<csv>&fields=id,timestamp,category(id),author(login),targetMember,target(idReadable,project(shortName))&$top=<n>&reverse=true`

| Category | Meaning / observed item type |
|---|---|
| `IssueCreatedCategory` | `IssueCreatedActivityItem`, `target.idReadable` set |
| `IssueResolvedCategory` | `IssueResolvedActivityItem`, `target.idReadable` set |
| `CommentsCategory` | `CommentActivityItem`; `target` is the comment, so the issue is **not** included |
| `CustomFieldCategory` | `CustomFieldActivityItem`; `targetMember` like `__CUSTOM_FIELD__State_2`; `added`/`removed` must be requested to see old/new values (needed to detect "reopened") |

- Item `id` is unique and monotonic per feed; `timestamp` is epoch ms. Use `timestamp` as the cursor and
  deduplicate by `id`.
- Comment items need an extra lookup (or the `target(issue(idReadable))` projection — **unverified**) to learn
  the issue ID.
- Activities are not project-filterable in the request I tested; filter by `target.project.shortName` or by
  querying issues. Using `project:` in the activity request is **unverified**.

## Knowledge base articles

`GET /api/articles?fields=idReadable,summary,updated,project(shortName),parentArticle(idReadable)&$top=n` and
`?query=<text>` answered `200` with `[]`. The test instance has **no articles**, so the field shapes (content,
hierarchy, attachments) are **unverified**. T20 must create a throwaway article first and record fixtures.

## Rate limits and errors

- No `X-RateLimit-*` or `Retry-After` headers were seen on any response; behaviour on `429` is **unverified**.
  The client should honour `Retry-After` if present and otherwise back off exponentially.
- The `X-Version` response header is an opaque hash, not the product version.
- Observed latencies: 10–50 ms for counts and issue pages, ~190 ms for the first articles call.

## Push updates (webhooks)

YouTrack has no built-in outgoing webhook configuration in the REST API. Pushing changes to Home Assistant would
need a YouTrack workflow using the `http` module. Not tested here; tracked in the stretch task for webhooks.

## Consequences for the implementation

1. Count client: retry on `-1`, never expose `-1` as a sensor value.
2. Error mapping: `401` → reauth, `403` → permission error, `404` may be HTML, `400 invalid_query` → validation
   error, network/timeout → unavailable.
3. Always pass `$top` and use `fields=` projections.
4. Overdue sensor depends on a due-date field; detect it per project.
5. Event entity: build from `CustomFieldCategory` (with `added`/`removed`) plus created/resolved/comment
   categories; verify comment-to-issue resolution first.
6. Knowledge base: shapes and permissions still to be verified (T20).
