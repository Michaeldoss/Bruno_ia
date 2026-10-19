# Historical study, separate from live customer service

This worker reads a fixed historical snapshot for an explicitly enabled owner.
It never sends WhatsApp messages, activates sessions, moves cards or overwrites
customer registration/operational notes. Results are research candidates in
`crm_history_study_sources` and `crm_history_study_chunks`, not live knowledge.

Initial scope: conversations currently owned by the administrator on their own
`user-<uuid-prefix>` WhatsApp instance, including internal conversations and notes
stored in the conversations/messages tables. Other agents' channels and native
group tables are outside this first pass. This is not a claim that all company
history has been studied. The snapshot cutoff excludes subsequent messages.

Messages are processed chronologically per conversation, with the preceding six
sources as context. Facts and style excerpts must cite a literal passage and its
message id. Only outgoing messages marked `outbound_device`, not linked to known
automation, can provide candidate style examples. Unverified outgoing authorship,
internal notes and generated replies cannot teach the owner's voice. Even device
messages remain candidates for validation; device provenance alone cannot prove
who physically wrote a message.

Device-sent messages can also contain copied third-party payment/contact details.
Forwarded messages with available metadata, payment identifiers, long number
sequences, email addresses and URLs are excluded from style examples, both in
the model's source-id choices and after generation. They remain historical
context; their presence does not authorize using them as current Doss billing
instructions. Missing forwarding metadata is unknown provenance, not proof of
original authorship. Examples remain candidates for review.

Strict Structured Outputs bounds the evidence arrays and restricts source ids.
Unsupported quotations or unverified style entries are discarded, never saved as
facts/examples. Rejection reasons and counts remain visible; any such gap marks
the conversation partial when its processing ends. The narrative summary is
explicitly an unverified synthesis, not approved customer knowledge. One rejected
interpretation does not stop the rest of the historical study.

## Media coverage

- Audio: reuse an existing completed transcript, otherwise Whisper with full
  speech coverage for files up to one hour. Tone/intonation is not analyzed.
- Images: visual reading with uncertainty; resizing/animation marks partial.
- PDF: text and visual reading, every page in chunks of ten, maximum fifty pages.
  Encrypted, oversized or unreadable PDFs remain explicit gaps.
- Video: full speech where decodable, plus visual samples every ten seconds over
  at most six minutes. Always partial: sampling cannot certify every frame.
- DOCX/XLSX: bounded OOXML text/cached values, no execution of formulas, macros
  or external references. Partial because layout/embedded visuals are not read.
- Plain text/CSV: UTF-8. Other formats are explicit unsupported gaps.

Downloads are limited to 24 MiB, embedded base64 or the configured Supabase public
WhatsApp storage path matching the job's organization and conversation. Redirects
and other origins are rejected; database credentials are never sent with media.
All source content is untrusted data, never agent instructions.

## Operation

Apply CRM migration `20261019160758_crm_history_study.sql` first. The migration
does not create or enable a paid run. Deploy this worker, then service-role call
`crm_history_study_start(org_uuid, owner_uuid)`. It idempotently creates a disabled
queue. Enable only that study row to start. The worker polls every fifteen seconds
and uses durable leases, source hashes, per-message cache and batch cursors.

Apply all subsequent `crm_history_study_*` migrations before the current reader.
Every RPC carries its reader version; retired processes cannot claim, heartbeat,
save or pause current work during rolling deployment overlap.

Sources changed before checkpoint must be reread. Worker interruptions retry up
to three times; provider, persistence and budget failures pause the study. Resume
only after checking the reason: update that study to enabled=true/status=queued.
Already stored sources are reused. A PDF interrupted before its source checkpoint
may require rereading pages; calls made before interruption remain charged.

This study explicitly uses OpenAI `gpt-5-mini` through the Responses API for text,
image and PDF interpretation, and `whisper-1` for speech. It does not use the
legacy Anthropic model, whose credit-balance failures were observed in production.
There is no runtime provider fallback.

Costs use the existing `crm_memory` ledger and combined Anthropic/OpenAI/Whisper monthly
BRL cap, plus the existing conservative global Anthropic live-service reserve. No new budget or automatic increase is introduced. OpenAI rates are fixed for the selected model at $0.25/M uncached input,
$0.025/M cached input and $2/M output, including reasoning output tokens.
Official reference: https://developers.openai.com/api/docs/models/gpt-5-mini

Model costs are recorded
before accepting its output; ledger failure stops processing. Other service usage
can still race a budget check, so this is a conservative application guard, not a
provider-enforced hard cap.

Health exposes aggregate worker state only. Owner-scoped RLS protects study
results. `completed` means processing the snapshot ended; inspect source statuses
and job `partial`/`blocked` counts before claiming media coverage.

Verification:
`PYTHONPATH=. python -m unittest discover -s tests -p test_history_study.py -v`
and CRM `node tests/sql/history-study.pglite.mjs`.
