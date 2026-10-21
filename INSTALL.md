# Installation

Start with [SELF-HOSTING.md](SELF-HOSTING.md) for a local or online copy of The Unstuckery.

The repository also retains the earlier macOS packaging tools (`desktop.py`, `onboarding.py`, `drive_storage.py`, `River.spec`, and `scripts/`). These require the macOS build dependencies and a trusted signing/notarization process for distribution. No prebuilt or signed macOS installer is included in this initial release. Legacy application-directory and database identifiers remain for compatibility.

For a local Google Drive handoff, stop the original app before importing its private data folder. Keep the original as a backup. A synchronized folder is not safe for simultaneous app instances: wait for syncing to finish, open only one writable copy, and check the handoff status. Cloud hosting avoids that cross-laptop folder handoff.
