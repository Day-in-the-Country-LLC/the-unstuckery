# The Unstuckery

**Big sigh. Small next step.**

A family-run homework space for students who benefit from taking one thing at a time—and parents who could use a little breathing room.

The Unstuckery helps families plan schoolwork, practice skills, ask a patient AI tutor questions, save answers, revise with feedback, and see their progress. A step-by-step exercise shows one instruction, checks the response, and opens the next step when the current one passes. Parent review stays separate from the student's workspace.

## Free code, your own AI key

This project is open source under the MIT license. Each family runs its own copy and supplies its own OpenAI API key. **LLM usage is billed by the provider to the family; it is not included or funded by this project.** Hosting may also cost money. There is no shared public signup service.

## Start here

Read [Self-hosting](SELF-HOSTING.md) for local setup or an online installation. The configuration helper asks for settings and saves them in ignored private files. Google Cloud infrastructure and a Linux service template are included in `deploy/`.

Online copies use Google sign-in. Approved parent accounts go to `/parent`; the approved student account goes to `/student`. Both pages and their APIs require authorization. This version supports **one family's student workspace per installation**. Use separate installations for separate student workspaces.

## What it includes

- Daily schoolwork, routines, timers, breaks, and parent-managed rewards.
- Extra practice with saved answers, private rubrics, feedback, revisions, and optional sequential steps.
- Feedback informed by the full relevant answer-and-feedback history for each question. Reviewers are instructed to adapt their coaching when earlier explanations did not help, while grading only the current answer.
- Unchanged retries return immediately without another AI review or attempt. Spacing alone is ignored; capitalization and punctuation edits can be reviewed. Scheduled later learning checks remain separate.
- A tutoring conversation alongside the current question, including earlier approved writing for context.
- Server-saved drafts that can continue on another device.
- Parent creation/import/publication of exercises and review of completed work.
- Optional read-only Canvas connections, subject to the school's permissions.
- A private assistant CLI for preparing drafts and recording observed evidence, plus optional delegated exercise publication with a separate planner key. Parent confirmations and account controls stay parent-only.
- Optional macOS packaging and a Google Drive folder handoff workflow for local installations.

Teacher work remains teacher work: an app checkmark does not submit anything to a school, establish a grade, or replace the school's required submission method. AI feedback is fallible; parents can review and correct it.

## Privacy and development

This repository contains application code and synthetic tests. Work, photos, account configuration, API keys, portal credentials, and deployment state belong in private storage. The initial public Git history starts fresh, without a family's earlier repository history.

`data/` and `private/` are ignored. The Docker build uses an explicit source-file list. Do not attach real student answers, screenshots, credentials, or database backups to public issues. See [Security](SECURITY.md) and [Contributing](CONTRIBUTING.md).

Some internal `river_*` names and the `river.sqlite3` filename remain for compatibility with existing private installations. They are schema identifiers, not bundled student records.

## Checks

Python 3.11+ and Node.js 20+:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.cjs
```

The normal application uses the Python standard library. Cloud Google sign-in adds the dependencies in `requirements-cloud.txt`; macOS packaging uses `requirements-build.txt`. Tests use isolated temporary data, with synthetic identities for authentication checks.
