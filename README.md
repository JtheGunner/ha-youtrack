<div align="center">

<img src="custom_components/youtrack/brand/icon@2x.png" alt="YouTrack for Home Assistant" width="96">

# 🎫 YouTrack for Home Assistant

**Bring your YouTrack projects, issues and knowledge base into Home Assistant.**

<code>YouTrack</code> &nbsp;→&nbsp; <code>Home Assistant</code> &nbsp;→&nbsp; <code>dashboards &amp; automations</code>

[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=flat-square)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-0ea5e9?style=flat-square&logo=homeassistant&logoColor=white)](https://www.home-assistant.io)
[![HACS](https://img.shields.io/badge/HACS-custom%20repository-f59e0b?style=flat-square)](https://hacs.xyz)
[![Status](https://img.shields.io/badge/status-early%20release-8b5cf6?style=flat-square)](CHANGELOG.md)

</div>

---

## ✨ Features

| | Feature | Status |
|---|---|---|
| 📊 | Issue statistics per project: total, open, resolved, unassigned, overdue, created and resolved in the last 7 days, critical open | ✅ available |
| 🚨 | Flags for *has overdue issues* and *has critical open issues* | ✅ available |
| 🔎 | Query sensors: any YouTrack search as a sensor, with the first issues as attributes | ✅ available |
| ✅ | Open issues as a Home Assistant to-do list | ✅ available |
| 🔔 | Events when issues are created, updated, resolved, reopened or commented | ✅ available |
| ✍️ | Actions to create issues, comment, change state, assign and log work | ✅ available |
| 📚 | Knowledge base: article count and latest change per project | ✅ available |
| 🔍 | Knowledge base: search and read articles from automations | ✅ available |

Each YouTrack instance is set up once; every project you add becomes its own device.

---

## 📦 Installation

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/JtheGunner/ha-youtrack` with the category **Integration**.
3. Install **YouTrack** and restart Home Assistant.

Requires Home Assistant **2026.10** or newer.

---

## 🔑 Create a token

1. In YouTrack open your profile, then **Account Security → New token**.
2. Give it the scope **YouTrack** and copy the token.

The token acts with the permissions of your account. It needs to read the projects and issues you want to
see in Home Assistant (*Read Project Basic* and *Read Issue* in those projects). Use a dedicated user if
you want to limit what Home Assistant can see. The actions and to-do changes additionally need permission
to create issues, comment, update issues and add work items.

---

## ⚙️ Setup

1. **Settings → Devices & services → Add integration → YouTrack**.
2. Enter the address of your YouTrack (include a context path such as `/youtrack` if your installation
   uses one) and the token. Disable certificate verification only for self-signed certificates.
3. Open the new entry and choose **Add project** for every project you want in Home Assistant.

You can change the address or token later with **Reconfigure**. If YouTrack rejects the token, Home Assistant
asks you for a new one.

### Configuration

| Where | Setting | Meaning |
|---|---|---|
| Setup | Address, token, verify certificate | How to reach YouTrack. The token needs the permissions described above. |
| Project | Project | The YouTrack project that becomes a device. |
| Project → Reconfigure | Query sensors | Add, edit or remove custom searches (name, query, number of listed issues). |
| Configure | Polling interval | How often YouTrack is asked, 1 to 60 minutes (default 5). |
| Configure | State for checked / unchecked items | The state set when a to-do item is checked or unchecked (defaults `Done` / `Open`). |
| Configure | Create issues from new to-do items | Off by default. |

### Removing the integration

1. Remove a single project with **Delete** on its entry under *Devices & services → YouTrack*.
2. Remove everything with **Delete** on the YouTrack entry. Its devices and entities go with it.
3. Revoke the permanent token in YouTrack under *Profile → Account Security* if you no longer need it.

---

## 🧩 Entities

Each project is a device with these entities. Entities that need a custom field the project does not have
are not created.

| | Entity | Meaning | Needs |
|---|---|---|---|
| 🔢 | Issues | All issues | |
| 📂 | Open issues | Unresolved issues | |
| ✅ | Resolved issues | Resolved issues | |
| 🙈 | Unassigned issues | Unresolved issues without assignee | field `Assignee` |
| ⏰ | Overdue issues | Unresolved issues past their due date | a date field named `Due Date` |
| ➕ | Created in the last 7 days | | |
| ✔️ | Resolved in the last 7 days | | |
| 🛑 | Critical open issues | Unresolved with priority `Show-stopper` or `Critical` | field `Priority` |
| 🚨 | Has overdue issues / Has critical open issues | On while the count above is greater than zero | as above |
| 📚 | Articles | Knowledge base articles of the project (`capped` attribute is `true` above 2000) | read access to articles |
| 🕑 | Last updated article | When an article last changed. Attributes: `id`, `title`, `author`, `url`, `recent` (latest five) | as above |

Knowledge base entities refresh at a sixth of the issue polling rate and stay unavailable, without affecting
the issue entities, when the token may not read articles.

### ✍️ Actions

| Action | Fields | Notes |
|---|---|---|
| `youtrack.create_issue` | `device_id` (project), `summary`, `description`, `type`, `priority`, `assignee` | Returns `issue_id` and `url`. |
| `youtrack.add_comment` | `issue_id`, `text` | |
| `youtrack.transition` | `issue_id`, `state`, `field` (default `State`) | |
| `youtrack.assign` | `issue_id`, `assignee` (login) | |
| `youtrack.log_work` | `issue_id`, `duration` (`1h 30m`), `text`, `date`, `work_type` | Units: `w`, `d`, `h`, `m`. |
| `youtrack.search_articles` | `query`, `device_id` (limit to a project), `limit` (1–50, default 10) | Response only. Returns `articles` with `id`, `title`, `project`, `author`, `updated`, `url`. |
| `youtrack.get_article` | `article_id` (`DEMO-A-12`), `max_length` (default 4000) | Response only. Returns the metadata plus Markdown `content` and `truncated`. |

`device_id` is optional for the issue actions as long as only one YouTrack instance is set up; otherwise pick
any project of the instance you mean. Type, priority, state and assignee are applied with YouTrack
commands, so values are matched like in the YouTrack command line (`In Progress`, not `"In Progress"`).
Values with quotes, braces or line breaks are refused. The token's user needs permission for the action;
if YouTrack denies it, the action fails with a clear message.

```yaml
action: youtrack.create_issue
data:
  device_id: <project device>
  summary: Heating pump is noisy
  priority: Major
response_variable: created
```

### ✅ To-do lists

Each project has an **Open issues** to-do list that works with the native to-do card, and every query sensor
gets a list with its issues. Items read `ID summary`; the description shows state, priority, assignee and
the link.

- **Checking** an item sets the issue to the state *State for checked items* (default `Done`);
  **unchecking** sets *State for unchecked items* (default `Open`). Both are options of the integration
  and must exist in your workflow.
- **Adding** items is off by default. Turn on *Create issues from new to-do items* to let new items of
  the *Open issues* list become issues of that project.
- Renaming an item is refused: edit the text in YouTrack.
- If the token's user may not change the issue, the card shows YouTrack's refusal.

### 🔔 Issue events

Every project has an **Issue activity** event entity and fires these events on the Home Assistant bus:
`youtrack_issue_created`, `youtrack_issue_updated`, `youtrack_issue_resolved`, `youtrack_issue_reopened`
and `youtrack_issue_commented`. The event data has `issue_id`, `summary`, `project`, `author`, `url` and
`entry_id`; `youtrack_issue_updated` adds `field`, `old_value` and `new_value`.

```yaml
automation:
  - alias: Notify about new issues
    triggers:
      - trigger: event
        event_type: youtrack_issue_created
    actions:
      - action: notify.notify
        data:
          message: "{{ trigger.event.data.issue_id }}: {{ trigger.event.data.summary }}"
```

History is never replayed: only activity after Home Assistant has started reading the feed is reported, so
changes made while Home Assistant was down are missed. Events arrive with the polling interval.

### 🔎 Query sensors

Open the project in **Settings → Devices & services → YouTrack**, choose **⋮ → Manage query sensors**
and add a named YouTrack search such as `#Unresolved for: me`. The sensor shows the number of matching
issues; its attributes list the first issues (default 10, at most 25) with id, summary, state, priority,
assignee and link. The query is limited to the project automatically.

> [!NOTE]
> YouTrack validates the syntax when you save, but accepts some unknown field names and simply matches
> nothing. Check a new query against the count you see in YouTrack.

> [!NOTE]
> Counts are computed by YouTrack. Right after the first request YouTrack may still be calculating a number;
> the sensor then shows *unknown* (or keeps its last value) until the next poll.

---

## 📚 Examples

| | Example | What it does |
|---|---|---|
| 🖥️ | [`docs/examples/dashboard.yaml`](docs/examples/dashboard.yaml) | A project overview: tiles, a gauge, the to-do list, history and activity. Replace `demo_project` with your project's entity prefix. |
| 📚 | [`knowledge-base-script.yaml`](docs/examples/knowledge-base-script.yaml) | A script that searches the knowledge base for a topic and sends the first matching article as a notification. |
| 🔔 | [`notify-critical-issues`](blueprints/automation/youtrack/notify-critical-issues.yaml) | Runs any action when the number of critical open issues goes up. |
| 🛠️ | [`issue-for-unavailable-entity`](blueprints/automation/youtrack/issue-for-unavailable-entity.yaml) | Creates a YouTrack issue when an entity has been unavailable for a set time. |

Import a blueprint in Home Assistant under **Settings → Automations & scenes → Blueprints → Import
blueprint** with the raw GitHub address of its file, or copy the file to
`config/blueprints/automation/youtrack/`.

---

## 📚 Using the knowledge base

The integration does not copy articles into Home Assistant. It counts them per project (see
[Entities](#-entities)) and reads them on demand through two actions that return their result as a response:

```yaml
- action: youtrack.search_articles
  data:
    query: heating manual   # YouTrack article search syntax
    limit: 3
  response_variable: found  # found.articles[*].id, .title, .url, ...
- action: youtrack.get_article
  data:
    article_id: "{{ found.articles[0].id }}"
    max_length: 2000        # longer content is cut and `truncated` is true
  response_variable: article  # article.content is Markdown
```

Searching with `device_id` limits the search to one project. The complete example in
[`docs/examples/knowledge-base-script.yaml`](docs/examples/knowledge-base-script.yaml) turns this into a script
you can call from automations or voice assistants.

---

## 📈 Load on YouTrack

The integration polls; it does not need webhooks or a plugin on the server. With the default 5 minute interval:

| Per poll | Requests |
|---|---|
| Each project | 7 count requests, 1 request for issues changed since the last poll (a small delta, not the full list) |
| Each custom query sensor | 2 more (count and the first issues) |
| Issue activity (events) | 1 for the whole instance, shared by all projects |
| Each project's knowledge base | 1 listing, but only every 6th poll |

| Projects | Requests per poll | About per second at 5 minutes |
|---|---|---|
| 1 | 9 | 0.03 |
| 5 | 41 | 0.14 |
| 20 | 161 | 0.54 |

- All requests of a project run together, so YouTrack sees one burst per project per interval. At most 6 requests
  are in flight at any time, however many projects you add.
- Every 12th poll the unresolved issues are reloaded completely (100 issues per request) to notice deleted
  or moved issues. A project with more than 1000 unresolved issues keeps the 1000 most recently changed ones in
  the to-do list and logs a warning; the counts stay exact because YouTrack computes them.
- If YouTrack answers *429 Too Many Requests*, the entities keep their last values and the next attempt
  waits for the time YouTrack asks for. Raise the polling interval under **Configure** to reduce load.

---

## 🧯 Troubleshooting

- **"The address did not answer like a YouTrack server"** – check the URL, including a possible context path.
- **A repair says a project no longer exists** – YouTrack no longer returns that project (deleted, or the
  token lost access). The other projects keep working. Confirm the repair to remove the project, or restore
  access and reload the integration; the repair then disappears. Renamed projects are picked up automatically.
- **Expired or revoked token** – Home Assistant asks for a new token (reauthentication) on the integration page.
- **Diagnostics** – download them from the integration page; the token and address are redacted.
- **Logs** – add `custom_components.youtrack: debug` under `logger:` in `configuration.yaml`. Remove tokens
  before sharing logs.

---

## 🤝 Contributing

Bug reports and ideas are welcome in the [issue tracker](https://github.com/JtheGunner/ha-youtrack/issues).
See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up the project and propose changes.

---

## 📝 Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## 📄 License

Released under the [MIT License](LICENSE).

---

<div align="center">
<sub>Not affiliated with or endorsed by JetBrains. YouTrack is a trademark of JetBrains s.r.o.</sub>
</div>
