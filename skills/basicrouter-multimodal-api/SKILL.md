---
name: basicrouter-multimodal-api
description: "Call the BasicRouter (basic-ware.ai) unified multimodal HTTP API — OpenAI-compatible chat/LLM, image generation/edit, and async video generation (text-to-video, image-to-video, digital-human). Use when building agents/skills that generate text, images, or video through BasicRouter, when discovering available model IDs, or when wiring the async video submit-poll pattern. Covers auth, the {code,message,data} envelope, the createVideo videoType matrix, and the /employee/models discovery quirk."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [basicrouter, api, video-generation, image-generation, kling, seedance, veo, llm-gateway, async-polling, digital-human]
prerequisites:
  commands: [curl, python3]
---

# BasicRouter Multimodal API

BasicRouter is a single OpenAI-compatible gateway exposing chat/LLM, image, and video models behind one key and one billing (Credits). This skill is the field-verified reference for calling it from skills/agents.

## Connection facts (verified 2026-07)

| Item | Value |
|---|---|
| Base URL | `https://api.basicrouter.ai/api`  (NOTE the trailing `/api`) |
| Auth | header `Authorization: Bearer <API_KEY>` on every request |
| Envelope | all non-SSE endpoints return `{ "code": 200, "message": "success", "data": {...} }`. `code=200` ok; `code=-1` = Insufficient credit; HTTP layer carries transport errors, envelope carries business errors |
| Billing | Credits; balance ≤ 0 → `Insufficient credit` |
| Docs | https://basicrouter.ai/docs (JS-rendered SPA — see pitfall below) |

Pitfalls that waste time:
- `basicrouter.ai` (no `api.`) serves the Vue SPA and returns HTML for any `/v1/...` path — you get a doctype, not JSON. The real API host is `api.basicrouter.ai` and every path is prefixed `/api`.
- The docs site is client-rendered; `curl` of the docs URL returns the SPA shell. Read doc text via a browser tool and `document.querySelector('main').innerText`, or just hit the live endpoints below.
- `model` field expects the **display name** (`modelName`, e.g. `kling-v3-omni-video`, `qwen3.6-plus`), not an internal UUID — the platform maps it internally.
- **Key scope trap (verified 2026-07)**: `/v1/models` always returns only LLM/text models (45 entries as of 2026-07), even on a key that has video/image access. Do NOT conclude that video models are unavailable from `/v1/models` alone. Always probe `GET /employee/models?category=video` to check video model access. If that also returns empty or no video models, the **key's account tier** is text-only — contact BasicRouter to upgrade/enable `kling-v3-omni-video` etc. Session keys are stored under `~/.cache/basicrouter/sessions/` and require the agent-neutral `BASICROUTER_SESSION_ID`; never fall back to a global key in normal agent sessions.

## Endpoints

| Capability | Method | Path | Mode |
|---|---|---|---|
| Chat / LLM | POST | `/v1/chat/completions` | OpenAI-compatible; supports `stream:true` (SSE, 300s window). **Use streaming for slow reasoning models** (kimi-k3) or the gateway drops the idle connection — see `br_client.chat_stream()` |
| Vision / multimodal chat (image INPUT) | POST | `/v1/chat/completions` | BasicRouter content-block format (`input_text`/`input_image`), NOT OpenAI vision format — see dedicated section below |
| Image gen / img2img | POST | `/ai/createImage` | sync → `data.imageUrls[]` |
| Video gen | POST | `/ai/createVideo` | **async** → `data.taskId` + `status` |
| Video poll | GET | `/ai/getVideoByTaskId?taskId=<id>` | poll every 5–10s → `videoUrl` when `status=succeeded` |
| Model discovery | GET | `/employee/models?category=video\|image\|text` | **no auth needed**; returns full catalog |

### Model discovery quirk (important)
`/v1/models` (OpenAI-style) returns **only chat/LLM** model IDs — video and image models are ABSENT there. To enumerate video/image models and their capabilities use `GET /employee/models?category=video` (and `=image`). That endpoint also returns per-model `videoDurationMin` and `allowVideoType`. See `scripts/probe_models.py`.

## Async video pattern (createVideo)

`videoType` matrix (each model advertises which it supports via `allowVideoType`):

| videoType | Meaning |
|---|---|
| 1 | text-to-video |
| 2 | image-to-video (first frame) |
| 3 | image-to-video (first+last frame) |
| 4 | image-to-video (reference image) — used for character/style/digital-human anchoring |
| 5 | multi-image reference (fuse product + person + scene) |

`urls` (array) is required whenever `videoType != 1`. Other body fields: `text` (prompt, required), `resolution` ("720p"), `ratio` ("9:16","16:9"), `duration` (seconds, integer ≥ model's `videoDurationMin`).

```bash
# submit
curl -s -X POST "$BASE/ai/createVideo" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{
    "model":"kling-v3-omni-video","videoType":4,
    "urls":["https://.../portrait.png"],
    "text":"...prompt...","resolution":"720p","ratio":"9:16","duration":8}'
# → data.taskId
# poll every 5-10s until data.status == succeeded, then read data.videoUrl
curl -s "$BASE/ai/getVideoByTaskId?taskId=$TASK" -H "Authorization: Bearer $KEY"
```
Poll cadence 5–10s; total wait 60–300s depending on model. `lastFrameUrl` also returned (useful to chain first→last-frame shots).

## Model selection cheat-sheet (catalog snapshot 2026-07, re-probe to refresh)

- **Most capable video / digital-human**: `kling-v3-omni` → `kling-v3-omni-video` (modelName) — supports videoType 1,2,3,4,5; min 3s. Best pick for reference-image / digital-human work.
- Other online video (2026-07 verified by `list_models(category='video')`): `seedance-2.0`, `seedance-1-5-pro`, `seedance-2.0-fast`, `veo-3.1-generate-001`, `veo-3.1-lite-generate-001`, `wan2.7-i2v`, `wan2.6-t2v`, `wan2.6-i2v-flash`, `happyhorse-1.0-t2v`, `happyhorse-1.0-i2v`, `happyhorse-1.0-r2v`, `gemini-omni-flash-preview`.
- Image models (2026-07): `imagen-4.0-ultra-generate-001`, `imagen-4.0-generate-001`, `gpt-image-2`, `gemini-3-pro-image-preview` (nano banana pro), `wan2.7-image-pro`, `seedream-4-5-251128` (seedream-4.5).
- Chat/script/creative: `qwen3.6-plus`, `claude-opus-4.x`, `gpt-5.x`, `glm-5.x`, etc. (`/v1/models` for the full live list).

**Important:** model `modelId` and `modelName` differ. The `modelName` is what you pass in the `model` field of API requests (e.g. `kling-v3-omni-video`, not the internal `kling-v3-omni`). Always use the `modelName` string from `list_models()` output.

## Multimodal / vision chat — image INPUT (verified 2026-07)

Sending images INTO a chat model (vision analysis, video-frame reverse-engineering, OCR-style reads) uses `/v1/chat/completions` but with **BasicRouter's own content-block format — NOT the OpenAI vision format.** Getting this wrong fails silently or 400s.

Content blocks (inside `messages[].content` as an array):
- Image block: `{"type":"input_image","image_url":"<url-string>"}` — `image_url` is a **flat string**, NOT the OpenAI nested `{"url":...}` object.
- Text block: `{"type":"input_text","text":"..."}` — NOT `{"type":"text",...}`.
- Images must be **reachable URLs** (their example uses an OSS CDN URL). Local frames must be uploaded first via `br_client.to_image_ref(path, api_key, prefer_hosted=True)` → https URL. base64 data URLs are for image-model (`createImage`) use, not reliably for vision chat input.

Response envelope for multimodal is DIFFERENT from plain chat:
- Plain text chat → top-level `{"choices":[{"message":{"content":"<str>"}}]}`.
- Multimodal (e.g. doubao/qwen-vl) → envelope `{"code":200,"data":{"message":{"content":[{"type":"output_text","text":"<str>"}]}}}` — text lives at `data.message.content[].text`, there is **no `choices`**.
- Always normalize both: check top-level `choices` first, then `_unwrap` → `data.choices`, then `data.message.content` (str OR list-of-`{text}`), then `data.content`/`data.text`. `br_client._extract_chat_text()` does exactly this — route all chat responses through it.

Picking a vision model (do NOT hardcode):
- The docs example model `doubao-seed-2-0-pro` is often **NOT deployed on the live gateway** (`Model not found: invalid_model`). Never hardcode a doc-example ID.
- Enumerate live vision models from `/employee/models?category=text` and keep those with `online=true` AND `json.loads(multimodelTypes)` containing `"image"`. Send the `modelId` friendly name.
- Verified-working vision IDs (2026-07): `kimi-k3` (preferred default — `multimodelTypes` = image+text+video, strong reasoning), `qwen3-vl-plus`, `qwen3-vl-flash`, `qwen3.6-plus`, `qwen3.7-plus`, `gpt-5.x`, `gemini-3-flash-preview`, `minimax-m3`, `glm-5v-turbo`. Safe fallback: `qwen3.6-plus` (widely online, faster).
- **Latency caveat**: `kimi-k3` is a heavy reasoning model — a 3–4 frame reverse-engineering call can take **several minutes** (well past the 120s default and even 240s). Give multimodal calls a generous client timeout (`br_client._request` uses 120s for chat; bump it or run the reverse step in the background). If you need speed over depth, prefer `qwen3-vl-plus`/`qwen3.6-plus` which respond in seconds.

Prompt-compliance pitfalls (learned building video-frame reverse-engineering):
- Put the **instruction text block FIRST, images after** (instruction-first). Images-first makes the model treat the picture as the subject and return a generic description instead of executing your task.
- For strict structured output (e.g. "produce a JSON scheme"), add a **`system` message** that pins the role and output contract ("your ONLY task is X, output MUST be a ```json block, do not produce generic descriptions, do not end with an offer to help"). Without a system message the model drifts into marketing-blurb analysis and omits the JSON.
- Even with a system message, models wrap the payload in unexpected top-level shapes: `remotion.timeline[]`, `remotion.scenes[]`, or (kimi-k3) top-level `sequences[]` with `from`+`durationInFrames` and text buried in `layers[].content` — instead of your `shots[]`. Write a **normalizer** that coerces all known alternate shapes into your canonical schema. Two timing traps: (1) parse `HH:MM:SS` timecodes → seconds; (2) **frame-based fields** (`from`/`startFrame`/`durationInFrames`) must ALWAYS be divided by fps — do NOT use a `value > 120 ? /fps : value` heuristic, because a legit `from:84` frame gets misread as 84 seconds. Key the conversion off the field NAME (frame-named → ÷fps) not the magnitude.

Long-request connection drop (verified 2026-07, kimi-k3):
- Heavy reasoning vision models (kimi-k3) spend **3–4 minutes** thinking before the first byte. A **non-streaming** POST gets its long-lived connection killed by the gateway → `Remote end closed connection without response` (same 524/499 family as basic-router idle-drop). This is NOT a model/format error — the request shape was accepted.
- Fix: **call with `stream:true` (SSE)** so the gateway emits `data: {...}` chunks that keep the socket alive (docs give streaming a 300s window vs shorter non-streaming). Parse both delta shapes: OpenAI `choices[].delta.content` AND envelope `data.message.content[].text`. `br_client.chat_stream()` does this and joins the deltas.
- Also note: `RemoteDisconnected` is a `ConnectionError` (from `http.client`), **not** a `urllib.error.URLError` — a retry loop that only catches `URLError` will miss it. Catch `(URLError, ConnectionError, EOFError)`.
- Pattern: try `chat_stream` first, fall back to non-streaming `chat(timeout=600)` if the stream errors or returns empty (some models don't stream).

```python
# frames on disk -> hosted URLs -> multimodal reverse-engineering call
content = [{"type": "input_text", "text": instruction_prompt}]   # instruction FIRST
for i, fp in enumerate(frame_paths):
    ref = br_client.to_image_ref(fp, api_key=KEY, prefer_hosted=True)  # local -> https
    content.append({"type": "input_text", "text": f"Frame {i+1}:"})
    content.append({"type": "input_image", "image_url": ref})          # flat string
system = {"role": "system", "content": "Your only task is per-shot reverse-engineering. Output timeline + one ```json block. No generic description."}
model = pick_vision_model()   # from /employee/models where multimodelTypes has "image"; default kimi-k3
msgs  = [system, {"role": "user", "content": content}]
try:                                             # STREAM FIRST — keeps long kimi-k3 call alive
    text = br_client.chat_stream(KEY, msgs, model=model, timeout=600)
except Exception:                                # fall back to non-streaming for models that don't stream
    resp = br_client.chat(KEY, msgs, model=model, timeout=600)
    text = br_client._extract_chat_text(resp)    # handles choices OR data.message.content[].text
```

## createImage body
`{model, text(prompt, required, non-empty — field name is "text" NOT "prompt"), count(int ≥1), resolution("2k"/"720p"), ratio("1:1"), imageUrls[](refs for img2img)}` → `data.imageUrls[]`.

**Key field names (verified from live API docs 2026-07):**
- Request body uses `text` (NOT `prompt`) for the generation prompt.
- Response data field is `imageUrls` (array of CDN URL strings).
- CDN URLs typically end in `.png` — do NOT hardcode `.jpg` as the download extension. Parse `os.path.splitext(url.split("?")[0])[1]` to get the real extension; fallback `.png`.
- Image models currently online (2026-07): `seedream-5.0`, `gpt-image-2`, `imagen-4.0-ultra-generate-001`, `imagen-4.0-generate-001`, `gemini-3-pro-image-preview` (aka "nano banana pro"), `wan2.7-image-pro`, `seedream-4-5-251128` (seedream-4.5).
- `gpt-image-2` (OpenAI DALL-E based) is available via BasicRouter. Works well for storyboard/cast-board images with a different style profile than seedream. Useful as an A/B alternative to seedream-5.0 in dual-model storyboard generation.

## Key handling for 0-base clients (skill UX pattern)
When shipping a skill that calls BasicRouter for a non-technical client, don't hardcode the key. The host agent must create one fresh `BASICROUTER_SESSION_ID` per new conversation and preserve it across subprocesses. On first run, prompt the user in chat to paste their `sk-...` key, validate it with one `GET /employee/models` call, persist it at 600 under the session cache, then reuse it silently across that session's skills. Key ownership stays with the client (clean billing + privacy).

## Verification / probing
- `scripts/probe_models.py` — lists online video+image models with duration/videoType/online flags.
- Quick auth check: a cheap `POST /v1/chat/completions` with `qwen3.6-plus` and `max_tokens:20` confirms the key authenticates before committing to expensive video calls.

## host_image utility (CDN-hosting a local file for use as a video URL reference)

`br_client.host_image(api_key, local_path)` uploads a local image to BasicRouter's CDN and returns a public HTTPS URL string. Use this when you have a portrait or product image on disk and need to pass it as `urls[0]` in a `createVideo` request.

```python
import br_client as br, os
KEY = key_setup.load_key()  # requires BASICROUTER_SESSION_ID in agent sessions
url = br.host_image(KEY, 'actors/momax/hostess-cantonese/portrait.png')
# url is a plain string like "https://s15-kling-fdl.klingai.com/..."
```

**Pitfall:** the returned CDN URL has a ~30-minute expiry. Generate and use it in the same session; do not cache it for later runs.

## Post-processing: concatenating segments with compose.py

After downloading all video segments, concatenate them with `compose.py concat`:

```bash
python3 scripts/compose.py concat \
  --inputs output/seg1.mp4 output/seg2.mp4 output/seg3.mp4 \
  --out output/final.mp4
# returns {"ok": true, "out": "output/final.mp4"}
```

For picture-in-picture (digital-human corner overlay on a background clip with mixed audio):
```bash
python3 scripts/fuse.py overlay \
  --bg background.mp4 --human avatar.mp4 \
  --slot corner --bg-volume 0.3 --out output/fused.mp4
# --bg-volume: BGM volume (0.3=ducked, 1.0=equal mix, 0.0=narrator only)
# returns {"ok": true, "out": "output/fused.mp4", "bg_volume": 0.3}
```
**Audio note:** `fuse.py overlay` mixes both audio tracks (main BGM + human narrator) via `amix`. Default `--bg-volume 0.3` ducks the BGM under the narrator. Previously `-map 1:a?` was used, which silently dropped the main audio track (BGM) — this was a bug fixed in v15.

See `references/api-reference.md` for the full field-level request/response spec captured from the docs.
