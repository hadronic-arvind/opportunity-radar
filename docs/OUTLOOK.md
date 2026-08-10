# Outlook and webhook options

## Recommended baseline

Use local macOS notifications for immediate alerts and use employer-created job alerts for email delivery.
This path requires no Microsoft tenant permission and stores no OAuth token.

## Optional Power Automate webhook

Opportunity Radar can send a compact JSON object to an HTTPS endpoint when a meaningful new item or newly failing source appears.
The payload contains a title, plain-language summary, up to ten opportunity records, and generic source errors.
It does not contain a local filesystem path, candidate identity, resume contents, or the webhook URL.

In Microsoft Power Automate, a user can create an HTTP request trigger and connect it to an Office 365 Outlook `Send an email (V2)` action.
HTTP-trigger and connector availability depends on the user's Microsoft license and tenant policy.
Microsoft documents the [Office 365 Outlook connector](https://learn.microsoft.com/en-us/connectors/office365/).

Store the generated HTTPS trigger URL in macOS Keychain with:

```bash
./scripts/configure_webhook.sh
```

The URL is never written to the repository, launch-agent file, SQLite database, dashboard, or routine log by the project.

## Microsoft Graph alternative

A direct Microsoft Graph sender would require an application registration, delegated sign-in, and a token lifecycle.
That complexity is intentionally outside the default runtime because local notifications solve the time-sensitive path without storing refresh tokens.

Add Graph delivery only after reviewing the relevant tenant policy and creating a clear token-revocation procedure.
See the official [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference).

## Safe test order

1. Complete a forced scan without notifications.
2. Confirm a local notification through one explicitly notified run.
3. Create a flow that sends only to an address controlled by the user.
4. Store the HTTPS URL through `scripts/configure_webhook.sh`.
5. Trigger one controlled test.
6. Confirm the secret does not appear in logs or output.
7. Leave change-only delivery enabled so empty scheduled scans stay silent.
