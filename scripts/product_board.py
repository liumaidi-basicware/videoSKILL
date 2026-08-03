#!/usr/bin/env python3
"""Generate a confirmed 3x3 product reference board for Seedance shots."""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import br_client  # noqa: E402
import key_setup  # noqa: E402
import product_library  # noqa: E402


DEFAULT_MODEL = "gpt-image-2"
IMAGE_MAX_WAIT = 900
IMAGE_RETRIES = 2


def _sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_fingerprint(reference_urls):
    """Bind a board candidate to the exact source images used to create it."""
    digest = hashlib.sha256()
    for value in reference_urls or []:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _state_path(out_dir):
    return os.path.join(out_dir, "product_board_state.json")


def _load_state(out_dir):
    path = _state_path(out_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _save_state(out_dir, state):
    path = _state_path(out_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def product_board_prompt(meta):
    product_type = meta.get("product_type") or "the exact product"
    product_color = meta.get("product_color") or ""
    style = meta.get("style_hint") or "commercial product reference photography"
    color_lock = (" PRODUCT COLOR LOCK: the exact product body is %s. Preserve this color in all nine panels; "
                  "natural highlights and shadows are allowed, but do not recolor or shift the product into another hue."
                  % product_color) if product_color else ""
    return (
        "Create one 16:9 landscape 3x3 product consistency reference board for %s. "
        "SOURCE-LOCK: the uploaded reference images are the sole authoritative identity source. "
        "Reconstruct new camera views of that exact physical object; do not substitute a generic item, "
        "redesign it, simplify it, or infer a different product from the text description. "
        "The exact same single product must appear in all nine panels with identical geometry, "
        "materials, colors, proportions, markings and distinctive details. "
        "Panel order: 1 front hero, 2 front-left 45 degree, 3 front-right 45 degree, "
        "4 strict left side, 5 strict right side, 6 rear, 7 top/controls, "
         "8 macro material/detail, 9 bottom/connection/detail. "
        "Use clean neutral backgrounds, controlled studio light except panel 9, sharp silhouette, "
        "Preserve every visible hinge, opening, control, seam, grille, connector, surface transition, "
        "case shape and color boundary from the references. Any product-specific housing, magnetic surface, "
        "charging port, button, display, hinge, connector or control geometry must match the uploaded "
        "reference exactly; do not invent a different product, generic accessory housing, alternate port layout "
        "or unrelated product parts. "
        "No extra products, no invented accessories, no readable generated text, no watermark or logo. "
         "All nine panels show only the product itself; no person, hand, usage scene or lifestyle context. "
        "This is a product identity board, not an advertisement poster. Style: %s.%s" %
        (product_type, style, color_lock)
    )


def generate(client, sku, *, model=DEFAULT_MODEL, out_dir=None):
    key = key_setup.load_key()
    if not key:
        raise br_client.BRError("No API key. Run key onboarding first.")
    meta = product_library._load_meta(client, sku)
    hero = os.path.join(product_library._sku_dir(client, sku), "hero.png")
    if not os.path.isfile(hero):
        raise br_client.BRError("产品缺少 hero.png，先导入真实产品图再生成产品九宫格。")
    out_dir = out_dir or product_library._sku_dir(client, sku)
    os.makedirs(out_dir, exist_ok=True)
    source_paths = [hero]
    source_paths.extend(
        product_library._view_path(client, sku, view)
        for view in product_library._DEFAULT_VIEW_ORDER
        if os.path.isfile(product_library._view_path(client, sku, view)))
    source_paths = list(dict.fromkeys(source_paths))[:4]
    reference_urls = [br_client.to_image_ref(path) for path in source_paths]
    source_fingerprint = product_library.source_fingerprint(client, sku)
    pending = os.path.join(out_dir, "product_board_pending.png")
    state = _load_state(out_dir)
    reusable = (os.path.isfile(pending) and os.path.getsize(pending) > 0 and
                state.get("source_fingerprint") == source_fingerprint and
                state.get("model") == model and
                state.get("board_sha256") == _sha256_file(pending))
    if not reusable:
        last_error = None
        result_url = None
        request_id = "product-board-" + reference_fingerprint(
            [source_fingerprint, model, "16:9", product_board_prompt(meta)])
        for attempt in range(IMAGE_RETRIES + 1):
            try:
                # gpt-image-2 reference-guided generation uses the synchronous
                # /ai/createImage imageUrls contract. The async endpoint accepted
                # data URLs but could silently produce text-only output.
                urls = br_client.create_image(
                    key, product_board_prompt(meta), model=model, count=1,
                    ratio="16:9", resolution="2k", image_urls=reference_urls,
                    request_id=request_id, timeout=600)
                if not urls:
                    raise br_client.BRError("产品九宫格生成未返回图片。")
                result_url = urls[0]
                br_client.download(result_url, pending)
                _save_state(out_dir, {
                    "status": "pending", "model": model,
                    "source_fingerprint": source_fingerprint,
                    "source_manifest": product_library.source_manifest(client, sku),
                    "reference_count": len(reference_urls),
                    "request_id": request_id,
                    "board_sha256": _sha256_file(pending),
                })
                break
            except Exception as exc:
                last_error = exc
                if result_url:
                    raise
                if attempt < IMAGE_RETRIES:
                    time.sleep(5 * (attempt + 1))
        else:
            raise last_error
    record = {"client": client, "sku": sku, "model": model, "status": "pending",
              "path": os.path.relpath(pending, ROOT), "prompt": product_board_prompt(meta),
              "source_fingerprint": source_fingerprint, "skipped": reusable}
    with open(os.path.join(out_dir, "product_board.json"), "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    return record


def generate_from_reference_urls(api_key, reference_urls, out_dir, *, product_type="the exact product",
                                 product_color="", style_hint="commercial product reference photography",
                                 model=DEFAULT_MODEL):
    """Generate a pending product board directly from uploaded plan references."""
    if not reference_urls:
        raise br_client.BRError("产品板至少需要一张用户上传的产品参考图。")
    os.makedirs(out_dir, exist_ok=True)
    meta = {"product_type": product_type, "product_color": product_color,
            "style_hint": style_hint}
    source_fingerprint = reference_fingerprint(reference_urls[:4])
    pending = os.path.join(out_dir, "product_board_pending.jpg")
    state = _load_state(out_dir)
    reusable = (os.path.isfile(pending) and os.path.getsize(pending) > 0 and
                state.get("source_fingerprint") == source_fingerprint and
                state.get("model") == model and
                state.get("board_sha256") == _sha256_file(pending))
    if reusable:
        return {"status": "pending", "model": model, "path": os.path.abspath(pending),
                "reference_count": len(reference_urls[:4]), "skipped": True,
                "source_fingerprint": source_fingerprint}
    last_error = None
    result_url = None
    request_id = "product-board-" + reference_fingerprint(
        [source_fingerprint, model, "16:9", product_board_prompt(meta)])
    for attempt in range(IMAGE_RETRIES + 1):
        try:
            urls = br_client.create_image(
                api_key, product_board_prompt(meta), model=model, count=1,
                ratio="16:9", resolution="2k", image_urls=reference_urls[:4],
                request_id=request_id, timeout=600)
            if not urls:
                raise br_client.BRError("产品九宫格生成未返回图片。")
            result_url = urls[0]
            br_client.download(result_url, pending)
            _save_state(out_dir, {
                "status": "pending", "model": model,
                "source_fingerprint": source_fingerprint,
                "reference_count": len(reference_urls[:4]),
                "request_id": request_id,
                "board_sha256": _sha256_file(pending),
            })
            break
        except Exception as exc:
            last_error = exc
            if result_url:
                raise
            if attempt < IMAGE_RETRIES:
                print("[gpt-image-2] product board retrying: %s" % exc, flush=True)
                time.sleep(5 * (attempt + 1))
    else:
        raise last_error
    return {"status": "pending", "model": model, "path": os.path.abspath(pending),
            "reference_count": len(reference_urls[:4]), "prompt": product_board_prompt(meta),
            "source_fingerprint": source_fingerprint}


def confirm(client, sku):
    directory = product_library._sku_dir(client, sku)
    pending = os.path.join(directory, "product_board_pending.png")
    confirmed = os.path.join(directory, "product_board.png")
    if not os.path.isfile(pending):
        raise SystemExit("未找到待确认产品九宫格: %s" % pending)
    state = _load_state(directory)
    current_fingerprint = product_library.source_fingerprint(client, sku)
    if state.get("source_fingerprint") != current_fingerprint:
        state.update({"status": "stale", "current_source_fingerprint": current_fingerprint})
        _save_state(directory, state)
        raise SystemExit("产品源图内容已变化，待确认产品板已失效，请重新生成。")
    os.replace(pending, confirmed)
    board_sha256 = product_library.file_sha256(confirmed)
    state.update({"status": "confirmed", "source_fingerprint": current_fingerprint,
                  "source_manifest": product_library.source_manifest(client, sku),
                  "board_sha256": board_sha256})
    _save_state(directory, state)
    record_path = os.path.join(directory, "product_board.json")
    record = json.load(open(record_path, encoding="utf-8")) if os.path.isfile(record_path) else {}
    record.update({"status": "confirmed", "path": os.path.relpath(confirmed, ROOT),
                   "source_fingerprint": current_fingerprint,
                   "board_sha256": board_sha256})
    with open(record_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description="Product 3x3 consistency board")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--client", required=True)
    gen.add_argument("--sku", required=True)
    gen.add_argument("--model", default=DEFAULT_MODEL)
    con = sub.add_parser("confirm")
    con.add_argument("--client", required=True)
    con.add_argument("--sku", required=True)
    args = parser.parse_args(argv)
    result = generate(args.client, args.sku, model=args.model) if args.command == "generate" else confirm(args.client, args.sku)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
