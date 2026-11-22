# Account-specific shared Honcho memory

This fork can route an explicitly configured administrator account to a
private Honcho connector, retaining native memory for every other account.
Set `HONCHO_USER_ID`, `HONCHO_CONNECTOR_URL` and `HONCHO_CONNECTOR_KEY` in the
deployment. Credentials and personal information do not belong in this repo.
Without enrollment configuration the native behavior is unchanged.

The existing model Memory capability controls availability. The additional
administrator-only **Allow Honcho memory updates** toggle is stored as
`meta.honcho_write_enabled`, default false. With Memory on and updates off,
the enrolled account gets recall and read tools only. With updates on,
eligible finalized conversations are queued and mutation tools are exposed.
The server rechecks the saved model entry at execution time. The start-of-turn
snapshot prevents enabling updates midway through an originally read-only
turn. Non-enrolled users' native memory does not use this setting.

Temporary chats, internal/direct requests, automations and non-chat tasks are
excluded. Regeneration and tool-resume capture are skipped rather than
backfilling older turns; a normal later turn still captures its new messages.
Use stable user/assistant message IDs, roles and original timestamps. Native
Responses prefetch retains the `recall_memories` compatibility name.

Personalization displays the shared profile and conclusions for the enrolled
account, with refresh and an explicit note that native rows are retained.
Native memory management remains available for non-enrolled users. The
shared profile view is not a full source-erasure UI: deleting a conclusion
does not erase conversations from which it may be derived.

The connector owns durable delivery/retry and upstream credentials. Request
failures preserve chat availability, log only generic error state, and must
not silently fall back to mutating native memory. Delivered source IDs are
retained for deduplication; exactly-once remote delivery is not promised.
Captured text is removed from delivered queue rows. Source edits after a
message was accepted do not rewrite existing Honcho messages automatically;
use a correction and inspect provenance when needed.

The application gives connector reads 20 seconds, exceeding its bounded
15-second upstream request. The connector reads profile/context and conclusion
lists concurrently. Successful memory tools identify their backend as Honcho;
failed reads return explicit unavailable status and must not be interpreted as
an empty memory store. Failed automatic prefetch also includes that distinction
in the memory context so the model does not silently substitute other sources.

Tests: `python -m unittest discover -s backend/tests -p 'test_honcho*.py' -v`
with the project's normal Python dependencies, an isolated environment and
no production database configuration. Deployment's connector has independent
fake-backend HTTP/queue protocol tests. Verify native function calling and
saved-chat reasoning/tool continuation before deployment.
