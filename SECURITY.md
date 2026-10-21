# Security and private data

Never post credentials, student work, photos, account emails, or database backups in a public issue. Use GitHub's private vulnerability reporting if enabled, or contact the repository owner privately for security concerns.

Online mode requires HTTPS and approved Google accounts. Sessions use secure, HttpOnly cookies, are stored as hashes, expire after 12 hours, and can be revoked. The server verifies Google signatures, audience, issuer, expiry, email authority, and a one-use login nonce. Role checks apply to API routes as well as pages. The old local PIN endpoints cannot unlock cloud mode.

One installation holds one student workspace. A parent can view and manage that workspace. Inviting a different student grants access to the same saved work; it does not create a separate workspace. This is not a multi-tenant service.

An LLM provider receives the submitted response and relevant exercise/tutoring context to provide feedback. Review the provider's account settings and terms. API keys and private rubrics stay server-side; the tutor is instructed not to reveal answer keys, but generated feedback still requires human judgment.

Keep OS and Python dependencies updated, restrict administration access, and maintain private backups. Local mode is loopback-only and intentionally has different authentication; it must never be exposed publicly.
