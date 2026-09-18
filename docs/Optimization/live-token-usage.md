# Live token usage

Query jobs now request OpenAI-compatible SSE from the existing `complete_chat`
gateway. Text and indexed tool-call argument fragments are assembled back into
the existing `LLMCallResult`; downstream chat, schema and agent code still receive
a complete result. Direct calls outside a query-job usage context remain non-streaming.

Configuration (backend settings, restart the backend after changing):

- `LLM_STREAM_USAGE=true` enables streaming for query jobs (default).
- `LLM_TIMINGS_PER_TOKEN=true` requests the llama.cpp extension (default).
  Set this to `false` for endpoints that reject this extension.
- `LLM_STREAM_USAGE=false` restores the non-streaming transport for providers
  that do not support streaming tools/schema or `stream_options.include_usage`.

Only actual provider counters qualify: intermediate `usage.completion_tokens`,
or llama.cpp `timings.predicted_n` (generated tokens). Prompt and total tokens,
text length, chunk count and estimates are never substituted. Counters received
only at or after `finish_reason` are retained in the final result but not shown
as live progress. Standard `include_usage` alone generally supplies final usage;
it does not guarantee intermediate counters. Availability of `timings_per_token`
depends on the installed llama.cpp version and endpoint implementation.

The existing query-job SSE carries `llm_usage` events, throttled to at most two
per second per model call. Each has a unique `detail.call_id` and a cumulative
`detail.completion_tokens` for that call. `llm_usage_finished` removes that call
on completion, cancellation, or failure. The UI sums measured counters of
currently generating calls. This is **active-call output usage**, not a whole-job
total; it can decrease or disappear between model calls. Missing live counters
remain hidden. Final usage continues through the existing LLM result/statistics
path. Request-local context isolates concurrent jobs and includes child agent calls.

2026-09-17 live validation: both deployed llama.cpp endpoints return intermediate
`timings.predicted_n` and final `usage.completion_tokens`. Real authenticated Job
SSE and browser rendering passed; the browser captured a change from 1 to 26
tokens and removed the status bar after fusion completed. Short calls can finish
before the next 0.5-second sample and therefore only show 1 briefly.
See [the live validation report](routing-stream-validation-2026-09-17.md) for
evidence, scope, reproducible commands, and outstanding UI regression failures.
