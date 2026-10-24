# Push updates through a YouTrack workflow

**Decision: no-go as a built-in feature for now. Polling stays the only mechanism; a push trigger can be
added by hand today, as described below.**

## Idea

A YouTrack workflow rule runs on every issue change and sends an HTTP request to a Home Assistant
[webhook](https://www.home-assistant.io/docs/automation/trigger/#webhook-trigger). Home Assistant then refreshes the
integration immediately instead of waiting for the next poll.

## Assessment

| Question | Answer |
|---|---|
| Does YouTrack support it? | Yes. Workflows can use the `http` module to call external URLs, on self-hosted installations and on YouTrack Cloud. |
| What does it buy? | Lower latency (seconds instead of up to one polling interval). It does **not** reduce load by itself: polling is already cheap (about 8 requests per project plus 1 per poll, see the README) and would have to stay as a fallback, because a missed webhook must never leave data stale. |
| What does it cost? | A workflow in every YouTrack project (admin rights), a URL YouTrack can reach, and a secret to manage. Many Home Assistant installations are not reachable from the YouTrack server. |
| Security | A Home Assistant webhook accepts unauthenticated requests; the long random webhook id is the only secret. It must travel over HTTPS only, and the payload must carry nothing sensitive (an issue id and project key are enough, since the integration reads the data itself). The id belongs in the workflow's settings, not in the script. A leaked id allows anyone to trigger refreshes (a nuisance, not a data leak). |
| Complexity inside the integration | Registering a webhook, keeping its id in the config entry, and mapping payloads to coordinators. This adds a configuration step and failure mode for a modest gain. |

## Why no-go

- The latency gain matters mainly for notifications, which can already be driven by the activity events with the
  polling interval set to one minute.
- Reachability and secret handling push real operational work onto every user.
- It cannot replace polling, so it only adds a second code path.

Revisit if users ask for sub-minute updates. The built-in version would be an opt-in option that registers a webhook
and requests a coordinator refresh when it is called.

## Do it by hand

This needs no change to the integration.

1. In Home Assistant create an automation with a **Webhook** trigger (local only off, POST only) and the action
   `homeassistant.update_entity` for one entity of each project, for example the *Open issues* sensor. Updating a
   coordinator entity refreshes the whole project.
2. In YouTrack add a workflow rule like the one below (**Administration → Workflows**) and attach it to the project.

```javascript
// Untested sketch: check the calls against the YouTrack workflow API reference of your version.
const entities = require('@jetbrains/youtrack-scripting-api/entities');
const http = require('@jetbrains/youtrack-scripting-api/http');

exports.rule = entities.Issue.onChange({
  title: 'Tell Home Assistant about changes',
  guard: (ctx) => ctx.issue.becomesReported || ctx.issue.isChanged('State') ||
    ctx.issue.comments.added.isNotEmpty(),
  action: (ctx) => {
    const connection = new http.Connection(ctx.settings.homeAssistantWebhookUrl);
    connection.addHeader('Content-Type', 'application/json');
    connection.postSync('', null, JSON.stringify({issue: ctx.issue.id}));
  },
  requirements: {},
});
```

Store the full webhook URL as a workflow setting (`homeAssistantWebhookUrl`) rather than in the script.

## Not verified

The decision rests on the YouTrack documentation. No workflow was installed on the test instance, because that
changes the server configuration, so the sample above has not been run.
