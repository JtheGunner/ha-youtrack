<div align="center">

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
| ✅ | Open issues as a Home Assistant to-do list (read-only for now) | ✅ available |
| 🔔 | Events when issues are created, updated, resolved, reopened or commented | ✅ available |
| ✍️ | Actions to create issues, comment, change state, assign and log work | 🛠 planned |
| 📚 | Knowledge base: search and read articles | 🛠 planned |

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
you want to limit what Home Assistant can see.

---

## ⚙️ Setup

1. **Settings → Devices & services → Add integration → YouTrack**.
2. Enter the address of your YouTrack (include a context path such as `/youtrack` if your installation
   uses one) and the token. Disable certificate verification only for self-signed certificates.
3. Open the new entry and choose **Add project** for every project you want in Home Assistant.

You can change the address or token later with **Reconfigure**. If YouTrack rejects the token, Home Assistant
asks you for a new one. **Configure** sets how often YouTrack is polled (default 5 minutes).

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

### ✅ To-do lists

Each project has an **Open issues** to-do list that works with the native to-do card, and every query sensor
gets a list with its issues. Items read `ID summary`; the description shows state, priority, assignee and
the link. The lists are read-only: completing an item in Home Assistant does not change YouTrack yet.

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

## 🧯 Troubleshooting

- **"The address did not answer like a YouTrack server"** – check the URL, including a possible context path.
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
