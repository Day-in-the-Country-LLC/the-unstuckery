# Run your own Unstuckery

You own the installation, its data, and its provider bill. Start with a local copy if you want to try it. An online copy works from different computers and keeps running when your laptop is off.

The current release supports one student workspace per installation. It is an early self-hosted application, not a managed service: online setup still benefits from a technical helper. Contributions that make this easier are very welcome.

## Try it on your computer

Install Python 3.11 or newer from python.org. Download this repository's ZIP from GitHub and extract it, or clone it. Open a terminal in that folder:

```sh
python3 configure.py
```

Answer **N** to the online question. Supply your own OpenAI API key, a model your project can access, and your time zone. The default model is `gpt-6.1-sol`; access depends on your provider project. The configuration helper hides the key as you type and writes `private/review.env` with owner-only permissions. You can leave the key blank to try planning without AI.

Start the local app:

```sh
python3 start.py
```

Open `http://127.0.0.1:4317`. Parent review asks you to choose a local PIN. The student side does not require a login in local mode; keep this mode on your own computer. Close the terminal to stop it. Do not expose local mode using a tunnel or port forwarding.

## Host online with Google sign-in

You need an HTTPS hostname, a small Linux server with a persistent disk, and your own Google web sign-in client. The app uses one process with a background review worker and SQLite. Run one replica. Do not put SQLite in an object-storage bucket or a shared network filesystem, and do not use a request-only server that suspends background work.

### Google identity setup

In Google Cloud's Google Auth Platform, configure a web application client. Add your site's exact HTTPS origin to **Authorized JavaScript origins** (for example `https://learning.example.com`). This app uses the JavaScript callback flow, so no redirect URI or client secret is needed. It requests identity only, not access to Drive, Gmail, or school portals.

Google's branding and audience settings apply to the whole project. Use a dedicated project if you need independent branding; avoid changing another app's shared branding. Follow Google's current testing/audience requirements. A school-managed Google account may be restricted by the school; use a parent-approved personal account if appropriate.

Run `python3 configure.py`, answer **Y**, and supply your hostname, web client ID, and parent email. The client ID is public; your configured family account list is private. The first parent signs in and adds the student's Google email in **Student sign-in** on `/parent`. Anyone else is denied access. The student's role cannot be chosen by changing the URL or a browser request.

### Linux with Docker Compose

Install Docker Engine and Compose using Docker's official instructions. Configure the app as above. Point your hostname's DNS A record to the server. Permit inbound TCP 80 and 443; keep SSH limited to your administration access.

The included Compose file uses Linux host networking so Python listens only on loopback and Caddy handles HTTPS. It is intended for a Linux server, not Docker Desktop on a Mac.

```sh
sudo chown -R 10001:10001 data private
sudo chmod 700 data private
sudo chmod 600 private/*
sudo docker compose up -d --build
sudo docker compose logs --tail=50 app https
```

Caddy obtains and renews a public certificate. Open your HTTPS address and sign in. Verify that a signed-out browser cannot open `/api/state` and that the student cannot open parent controls. Retain Caddy's named volumes so certificates survive updates.

### Google Cloud VM

`deploy/gcp/` creates a dedicated network, subnet, external address, two narrow firewall rules, and one `e2-micro` Debian VM with a 30 GB standard persistent disk in `us-central1-a`. SSH is reachable through Google IAP, not directly from the Internet. The VM has no attached Google service account. Deletion protection is enabled; the disk is retained if the VM is removed.

Google's free allowance may cover the VM and disk; the external IPv4 address, backups, and traffic can still cost money. Check current billing and account-wide allowances. The templates do not configure a billing alert or guarantee a monthly cap.

Supply `project` and an administration `ssh_public_key` using an ignored `install.auto.tfvars`. Authenticate Terraform with your own Google credentials, then:

```sh
terraform -chdir=deploy/gcp init
terraform -chdir=deploy/gcp validate
terraform -chdir=deploy/gcp plan -out=tfplan
terraform -chdir=deploy/gcp apply tfplan
```

Review the plan before applying. The bootstrap installs Python, Caddy and security updates; it deliberately leaves Caddy stopped until the app is installed.

Place public source in `/opt/unstuckery/app`, create `/opt/unstuckery/venv`, and install `requirements-cloud.txt` there. Private data goes in `/srv/unstuckery/data`; settings go in `/srv/unstuckery/private`. Both must be owned by `unstuckery` and mode 700, with private settings mode 600.

Install `deploy/unstuckery.service` as `/etc/systemd/system/unstuckery.service`. Set the literal hostname in a copy of `deploy/Caddyfile.example` at `/etc/caddy/Caddyfile`. Then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now unstuckery
sudo systemctl enable --now caddy
```

Keep your private Terraform state and variables outside the public repository. Do not share a deployment plan containing your settings.

## Settings and limits

`private/review.env` accepts `OPENAI_API_KEY`, `OPENAI_REVIEW_MODEL`, and `RIVER_AI_MAX_REVIEWS_PER_DAY` (0–100). Set it to `0` to allow unlimited daily reviews and tutor replies, billed to your own API key. Removing the cap also resumes saved requests that were blocked before reaching the provider. The default is 30 daily requests; this is a usage guard, not a dollar budget. Provider billing limits are separate. Restart the server after changing these settings.

`private/installation.env` accepts `UNSTUCKERY_TIMEZONE`, `UNSTUCKERY_CANVAS_ORIGIN`, and `UNSTUCKERY_DOMAIN`. The Canvas origin must be the exact HTTPS origin for the school. Credentials are stored privately, and outbound Canvas operations are restricted to read-only endpoints at that origin. Parent/student Google sign-in is separate from school authentication. A school-issued Canvas OAuth client or permitted parent API token may be needed; do not assume every school permits integration.

Exercise creation/import is available in Parent review. An external assistant can also use `assistant.py` and the private `.assistant-token` to prepare drafts; it cannot publish or use parent controls. For a cloud installation, keep that token private, save the exact HTTPS origin in `.assistant-origin` beside the token, and use `--url` with that origin. Redirects are refused to prevent forwarding the credential elsewhere.

### Optional exercise planner access

An installation owner can delegate exercise preparation and publication without sharing a Google login. This is off by default. Set `UNSTUCKERY_PLANNER_ENABLED=1` in the server's private `installation.env` and restart. The server creates an owner-only `data/.planner-token`. Transfer it privately to the assistant's credential directory and pin the exact HTTPS address in `.planner-origin` beside it. Never put the key in a URL, browser, student material, chat message, or Git.

Use the same CLI with `--planner --data-dir /private/credential-directory --url https://learning.example.com`:

```sh
python3 assistant.py --planner --data-dir /private/credential-directory --url https://learning.example.com state --date 2026-10-21
python3 assistant.py --planner --data-dir /private/credential-directory --url https://learning.example.com practice-draft --json-file exercise.json
python3 assistant.py --planner --data-dir /private/credential-directory --url https://learning.example.com practice-publish QUIZ_ID --json-file publication.json
```

Drafts use the usual exercise JSON plus a unique `requestId` (letters, digits, hyphens, underscores). Retrying the same request ID and content returns the existing draft; different content with that ID is rejected. `practice-update-draft QUIZ_ID --json-file exercise.json` requires the current `expectedRevision` and works only on the planner's own unpublished, unstarted drafts. Read revisions through `practice-state`.

Publication JSON contains `expectedRevision`, integer `plannedMinutes` (1–90), and student-facing `instructions`. It creates one extra-practice task on the draft's date, rejects past dates and plans over 90 minutes, and is safe to retry unchanged. Publication requires complete rubrics and assistant-reviewed responses. Existing published work cannot be overwritten through this API. A paused exercise remains the same task.

The planner can read school-plan metadata and private practice material/progress to plan appropriate exercises. It cannot read account configuration or photos, use parent or student endpoints, grade answers, change global practice settings, report readiness, complete tasks, confirm chores, change timers, or award prizes. Its actions are recorded as `planner`, never as a parent. Adding an expected task does not newly mark the day's plan parent-reviewed. Parent completion confirmation remains required.

To revoke access, set `UNSTUCKERY_PLANNER_ENABLED=0` and restart. To rotate the key, stop the app, move the old `.planner-token` into a protected backup, then start with planner access enabled and transfer the newly generated token. Old keys stop working after restart. No SQL migration is needed: optional `plannerRequests` and publication/audit fields live in the existing JSON state. For rollback, disable planner access and follow the source/data backup procedure below; do not discard student work to remove the feature.

## Back up, update, and restore

Back up the **whole private data directory**, private settings, and any separately configured Canvas credential file. Keep encrypted or access-restricted copies outside the server. A persistent disk is not a backup. Stop the app while copying a database, or use SQLite's backup API for a consistent snapshot.

Before upgrading, stop the app and make a dated backup. Replace only the source code; keep `data/`, `private/`, and Caddy volumes. Restart and check sign-in, saved work, and review status. To roll back, stop the app, restore the matching source and data backup, then restart. Never run old and new copies against the same database at the same time.

For migration from an earlier local installation, make a consistent SQLite backup and transfer the existing data privately. Do not import real student data through Git. If the local browser contains an unsent draft, open it and save it before moving; older versions may have kept drafts only in that browser. Old Canvas OAuth sessions may need reconnecting at the new HTTPS callback address.
