#!/bin/bash
set -euo pipefail

echo "Paste an HTTPS webhook URL for an optional Outlook or Teams flow when Keychain prompts."
echo "The secret will be stored in macOS Keychain, not in this project or a process argument."

CURRENT_USER="$(/usr/bin/id -un)"
/usr/bin/security add-generic-password -a "$CURRENT_USER" -s OpportunityRadarWebhook -U -w
if ! /usr/bin/security find-generic-password -a "$CURRENT_USER" -s OpportunityRadarWebhook -w |
  /usr/bin/grep -Eq '^https://[^/?#[:space:]]+([/?#][^[:space:]]*)?$'; then
  /usr/bin/security delete-generic-password -a "$CURRENT_USER" -s OpportunityRadarWebhook >/dev/null
  echo "The webhook must be an absolute HTTPS URL." >&2
  exit 1
fi
echo "Webhook saved in Keychain."
