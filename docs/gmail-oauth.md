# Gmail OAuth setup (standalone local app)

The Gmail adapter runs dry by default. To enable real draft creation, follow
Google's official quickstart and configure a desktop OAuth client.

## 1. Create OAuth credentials

1. Go to https://console.cloud.google.com/apis/credentials
2. Create a project, then **OAuth consent screen** (External, test mode is fine).
3. Add yourself as a test user.
4. **Create credentials → OAuth client ID → Desktop app**.
5. Download the JSON, save it to a **git-ignored** path, e.g.
   `~/.config/automatejob/gmail-credentials.json`, and `chmod 600` it.

Scopes actually used (least privilege):
- `https://www.googleapis.com/auth/gmail.compose` — create/manage drafts
- (optional) `https://www.googleapis.com/auth/gmail.readonly` — thread/reply detection

## 2. Authorize once (interactive step — run manually)

The project includes the official installed-app OAuth flow. After downloading
`gmail-credentials.json`, authorize from the local desktop session:

```bash
export GMAIL_CREDENTIALS_FILE=~/.config/automatejob/gmail-credentials.json
export GMAIL_TOKEN_FILE=~/.config/automatejob/gmail-token.json
chmod 600 "$GMAIL_CREDENTIALS_FILE"
python3 -m automatejob gmail-auth
```

This opens a browser consent screen, verifies the selected Gmail account, and
writes `GMAIL_TOKEN_FILE` with mode `0600`. It is never called by the collector,
cron job, or dashboard startup. If it fails or you cancel consent, no token is
created and the copy-email workflow remains available.

Neither file is ever committed (both are git-ignored). Tokens are never logged.

## 3. Use

```bash
export GMAIL_CREDENTIALS_FILE=~/.config/automatejob/gmail-credentials.json
export GMAIL_TOKEN_FILE=~/.config/automatejob/gmail-token.json
python3 -m automatejob dashboard --token <your-token>    # create drafts
python3 -m automatejob dashboard --token <your-token> --enable-send   # send (requires per-message approval)
```

A draft created in Gmail is NOT a sent email. Sending stays disabled unless
`--enable-send` is passed AND you explicitly approve the exact recipient,
subject and body of each message. After OAuth, run `python3 -m automatejob
gmail-sync` manually to check tracked Gmail threads for replies; it only records
reply state and suppresses follow-ups, and never sends email.

## Without OAuth: copy-email workflow

If you do not configure OAuth, use the manual path:

1. Approve a draft in the dashboard.
2. Copy its subject + body and send it from your own email client.
3. Click **Mark sent manually** (or `python3 -m automatejob mark-sent --draft-id N`),
   which records the actual send timestamp and starts follow-up tracking.

Follow-ups still require per-message approval and stop on reply/rejection/bounce/opt-out.
