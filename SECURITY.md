# Security and private data

Never post credentials, student work, photos, account emails, or database backups in a public issue. Use GitHub's private vulnerability reporting if enabled, or contact the repository owner privately for security concerns.

Online mode requires HTTPS and approved Google accounts. Sessions use secure, HttpOnly cookies, are stored as hashes, expire after 12 hours, and can be revoked. The server verifies Google signatures, audience, issuer, expiry, email authority, and a one-use login nonce. Role checks apply to API routes as well as pages. The old local PIN endpoints cannot unlock cloud mode.

One installation holds one student workspace. A parent can view and manage that workspace. Inviting a different student grants access to the same saved work; it does not create a separate workspace. This is not a multi-tenant service.

Optional planner access uses its own owner-only bearer key and an explicit HTTP route allowlist. It is disabled unless the installation owner enables it. It can read practice context and publish its own new exercises, but cannot authenticate as a parent/student, approve answers or completion, change accounts, or grant rewards. Delegated publication is audited separately from parent approval. Keep this key and its pinned origin private; see Self-hosting for revocation and rotation. The older assistant token does not gain publication privileges.

An LLM provider receives the submitted response and relevant exercise/tutoring context to provide feedback. Review the provider's account settings and terms. API keys and private rubrics stay server-side; the tutor is instructed not to reveal answer keys, but generated feedback still requires human judgment.

Keep OS and Python dependencies updated, restrict administration access, and maintain private backups. Local mode is loopback-only and intentionally has different authentication; it must never be exposed publicly.

Optional assistant manager access uses a third, separate private bearer key and
an explicit `/api/manager/` route allowlist. It adds audited unfinished-plan edits,
work-time corrections and failed-review retries to delegated exercise publishing.
It can also archive a parent's explicitly withdrawn unfinished steps in sequential
practice, preserving original questions and saved responses without assigning a
pass or rewriting an active rubric. Revision and concurrency checks apply.
It does not confer parent/student identity, completion approval, grading, account,
credential, reward, or command-execution powers. Writes require reasons, durable
idempotency IDs, and current revisions for existing records. See Self-hosting for
the exact scope, enablement, revocation, and rotation procedure.
