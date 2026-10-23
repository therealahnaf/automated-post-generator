# YouTube authorization

Standalone authorization only: this does not upload videos or change posting workflows.
Create a Google Desktop OAuth client and enable YouTube Data API v3. Put
`YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` in the ignored repository-root `.env`.

Install `tools/youtube/requirements.txt` into the Python environment, then run:

```powershell
.\.venv\Scripts\python.exe tools/youtube/authorize.py
```

Select the intended channel in the browser. Upload and read-only YouTube permissions
are requested so the helper can verify the authorized channel. The refresh token,
channel ID, and scope list are saved only in `.env`; access tokens are not persisted
or printed. The helper refuses to replace an existing authorization unless
`--reauthorize` is provided. Pass `--expected-channel-id UC...` to enforce a channel
on initial authorization. Otherwise check the reported channel title and ID.

Check existing authorization without opening a browser:

```powershell
.\.venv\Scripts\python.exe tools/youtube/authorize.py --status
```

For a headless Linux VM, securely transfer only the five `YOUTUBE_` settings into
its private `.env` (not the entire local `.env`). Protect that file with mode 600.
No browser consent is needed there while the refresh token remains valid.

To authorize on the VM instead, forward local port 8765 over SSH and run the
helper there with `--no-browser --port 8765`. Open its URL on the local computer
while leaving the SSH connection open. No public firewall port is needed.

External consent apps in Testing have seven-day refresh tokens for these scopes.
Switch to Production for ongoing automation; tokens can still be revoked or expire.
Never commit `.env`, paste tokens into chat, or pass credentials as CLI arguments.
