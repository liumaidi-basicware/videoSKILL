# BasicRouter API — field-level reference

Captured from https://basicrouter.ai/docs (last doc update 2026-05-25) plus live probing 2026-07. Concise, task-value oriented — not a full mirror.

## Auth & envelope
- Header: `Authorization: Bearer <API_KEY>` (required on all).
- All non-SSE responses: `{ "code": <int>, "message": <str>, "data": <obj|array|null> }`.
- Error codes: 200 ok · 400 bad params · 401 unauthorized/expired · 403 no permission · 404 not found · 429 rate-limited · 500 server error · **-1 Insufficient credit (top up)**.
- Timeout guidance: normal 30s · image 120s · SSE stream 300s · video = async polling.

## 5.1 Chat (OpenAI-compatible) — POST /v1/chat/completions
Standard OpenAI body: `{model, messages:[{role,content}], max_tokens, stream, stream_options}`.
- Multimodal input (per docs): `content` array with `{"type":"image_url","image_url":{"url":...}}` / `{"type":"video_url",...}` + `{"type":"text","text":...}`. ⚠️ VERIFIED 2026-07 the live gateway wants the FLAT variant instead — `{"type":"input_image","image_url":"<url-string>"}` + `{"type":"input_text","text":...}` — and returns text at `data.message.content[].text` (no `choices`). See the main SKILL.md "Multimodal / vision chat" section; trust that over this doc capture.
- `stream:true` → SSE `data: {...chat.completion.chunk...}` lines; use a POST+SSE client (e.g. @microsoft/fetch-event-source in JS). Response shape is standard OpenAI (`choices[].message.content`, `usage`).

## 5.3 Image — POST /v1/image-generations
Body:
| field | type | req | note |
|---|---|---|---|
| model | string | yes | e.g. seedream-5.0, kling-v3-omni-image, nano banana pro |
| text | string | yes | prompt, non-empty |
| count | int | yes | ≥1, default 1 |
| resolution | string | no | e.g. "720p", "2k" |
| ratio | string | no | e.g. "1:1", "16:9" |
| imageUrls | array<string> | no | reference images (img2img / style transfer) |

Success `data`: `{ "taskId": "..." }`. Retrieve with `GET /v1/image-generations/{taskId}` until succeeded; generated image URLs are returned in the task result. Image edit/reference-guided generation uses the same async submit+retrieve pattern with `imageUrls`.

## 5.4 Video (async) — POST /v1/video-generations
Body:
| field | type | req | note |
|---|---|---|---|
| model | string | yes | video modelName |
| text | string | yes | prompt, non-empty |
| videoType | int | yes | 1=t2v · 2=i2v(first) · 3=i2v(first+last) · 4=i2v(reference) · 5=multi-image ref |
| imageUrls | array<string> | cond | required when videoType != 1 |
| resolution | string | no | "720p" |
| ratio | string | no | "16:9","9:16" |
| duration | long | no | seconds, positive int, ≥ model's videoDurationMin |

Success `data`: `{ "taskId": "...", "status": "submitted|processing|succeeded|failed" }`.

## 5.5 Poll — GET /v1/video-generations/{taskId}
`data`: `{ status, videoUrl (when succeeded), lastFrameUrl (optional), message }`. Poll every 5–10s; max wait 60–300s per model.

## 5.6 Model list — GET /employee/models?category=text|image|video
No auth required. Per-model fields worth using:
`modelName` (use THIS as the `model` value) · `provider` · `category` · `status`/`online` (both must be true to use) · `isDefault` · `videoDurationMin` · `allowVideoType` (which videoTypes the model supports) · `imageCount` (max images/call) · `modelPrices[]`.
Reminder: `/v1/models` (OpenAI style) returns chat models only — use `/employee/models` for image/video discovery.

## Notes from platform docs
- `model` must be the platform display name; internal UUID mapping is automatic.
- Concurrency per key is limited; excess is queued/rejected — contact platform for quota.
- Some models require whitelisting ("开白") — contact support.
- Report bugs with the `X-Request-Id` response header + model ID + minimal repro.

## Catalog snapshot (2026-07, re-probe with scripts/probe_models.py to refresh)
Video (online): kling-v3-omni-video [1,2,3,4,5, min3s], seedance-2.0 / -2.0-fast [1,2,3,5], seedance-1-5-pro [1,2,3], veo 3.1 [1,2,min4], veo 3.1 lite [1,2,min8], wan2.7-i2v [2], wan2.6-t2v [1,min2], wan2.5-i2v-preview [2], HappyHorse-1.0-{t2v/i2v/r2v}. (gemini-omni-flash-preview present but offline.)
Image (online): imagen 4 ultra/standard/fast, gpt-image-2, nano banana pro / nano banana 2, wan2.7-image(-pro), seedream-4.5 / 5.0, qwen-image-max/plus/2.0-pro, kling-v3-omni-image, kling-image-o1.
