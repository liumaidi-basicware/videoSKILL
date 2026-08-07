#!/usr/bin/env python3
"""Digital-human actor library: create / list / bind branded outfit / resolve.

Layout:
  actors/<client>/<actor>/
    portrait.png            standard face/body reference
    meta.json               {name, client, gender, style, language, status, default_outfit}
    refs/                   extra angle refs (videoType=5 multi-ref)
    outfits/<outfit>/
      portrait.png          actor wearing branded garment
      logo_spec.json        {logo_file, position, scale, margin}
      refs/

Actor images can be (A) client real photo (place file, run 'create --from-file')
or (B) AI-generated via kling-v3-omni-image/seedream (run 'create --generate').

CLI:
  python3 digital_human.py list [--client <client>]
  python3 digital_human.py create --client <client> --actor hostess-cantonese \
      --generate "港式親和女主播，專業妝容，純色背景，正面半身" --gender female --language yue
  python3 digital_human.py create --client <client> --actor boss --from-file /path/photo.png
  python3 digital_human.py confirm --client <client> --actor boss
  python3 digital_human.py resolve --client <client> --actor hostess-cantonese [--outfit polo-logo]
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ACTORS_DIR = os.path.join(ROOT, "actors")
sys.path.insert(0, HERE)
import br_client            # noqa: E402
import key_setup            # noqa: E402
import ux                   # noqa: E402
from project_utils import validate_client, validate_component  # noqa: E402


def _actor_dir(client, actor):
    validate_client(client)
    validate_component(actor, "actor")
    return os.path.join(ACTORS_DIR, client, actor)


def list_actors(client=None):
    out = []
    if not os.path.isdir(ACTORS_DIR):
        return out
    clients = [client] if client else sorted(os.listdir(ACTORS_DIR))
    for cl in clients:
        cdir = os.path.join(ACTORS_DIR, cl)
        if not os.path.isdir(cdir):
            continue
        for actor in sorted(os.listdir(cdir)):
            adir = os.path.join(cdir, actor)
            meta_path = os.path.join(adir, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            outfits = []
            odir = os.path.join(adir, "outfits")
            if os.path.isdir(odir):
                outfits = [o for o in sorted(os.listdir(odir))
                           if os.path.isfile(os.path.join(odir, o, "portrait.png"))]
            meta["outfits"] = outfits
            out.append(meta)
    return out


# Persona attributes the co-creation dialogue collects and this library stores.
# All optional; free-form Chinese/English strings written by the local model.
PERSONA_FIELDS = ["personality", "profession", "age", "hairstyle",
                  "appearance", "makeup", "voice_type", "expression",
                  "body_language"]
_PERSONA_LABEL = {
    "personality": "性格", "profession": "职业", "age": "年龄",
    "hairstyle": "发型", "appearance": "样貌", "makeup": "妆容",
    "voice_type": "声音类型", "expression": "讲解表情",
    "body_language": "表演基调/肢体语言",
}


def compose_portrait_prompt(gender="", language="", style="", persona=None):
    """Build an image-generation prompt from the collected persona attributes,
    so 发型/样貌/妆容/职业气质/表情 all shape the generated portrait.

    Returns (prompt, is_generic). is_generic=True means gender/style/persona were
    ALL empty, so the prompt is just the hardcoded fallback suffix ("专业商业人像，
    正面半身，纯色背景，柔和布光，高清写实") with zero actual customization from the
    caller — this silently produces a generic stock-photo-style portrait with no
    signal that nothing was actually specified. Callers (create_actor) must not
    silently proceed on is_generic=True without an explicit opt-in, matching the
    codebase's "no silent degradation" convention (allow_text2video/allow_ocr_warning).
    """
    persona = persona or {}
    parts = []
    if persona.get("profession"):  parts.append(persona["profession"])
    if gender:                     parts.append({"female": "女性", "male": "男性"}.get(gender, gender))
    if persona.get("age"):         parts.append(persona["age"])
    if persona.get("appearance"):  parts.append(persona["appearance"])
    if persona.get("hairstyle"):   parts.append("发型:" + persona["hairstyle"])
    if persona.get("makeup"):      parts.append("妆容:" + persona["makeup"])
    if persona.get("personality"): parts.append("气质:" + persona["personality"])
    if persona.get("expression"):  parts.append("表情:" + persona["expression"])
    if persona.get("body_language"): parts.append("体态:" + persona["body_language"])
    if style:                      parts.append(style)
    is_generic = not parts  # nothing from gender/style/persona actually contributed
    parts.append("专业商业人像，正面半身，纯色背景，柔和布光，高清写实")
    return "，".join([p for p in parts if p]), is_generic


def create_actor(client, actor, gender="", style="", language="",
                 generate_prompt=None, from_file=None, persona=None, api_key=None,
                 allow_generic=False):
    """Create an actor from a generated image or an existing photo file.

    persona = dict with any of PERSONA_FIELDS (性格/职业/发型/样貌/妆容/声音类型/表情...).
    If generating and no explicit generate_prompt is given, one is composed from persona.

    allow_generic: when generating (no from_file/generate_prompt) and gender/style/persona
    are ALL empty, compose_portrait_prompt() would produce a fully generic stock-photo
    prompt with zero customization. Per the "no silent degradation" house rule (same
    pattern as allow_text2video/allow_ocr_warning), this is BLOCKED by default — caller
    must either supply persona/gender/style, or pass allow_generic=True to explicitly
    opt into a generic portrait (e.g. throwaway/placeholder actor).
    """
    persona = persona or {}
    adir = _actor_dir(client, actor)
    os.makedirs(os.path.join(adir, "refs"), exist_ok=True)
    portrait = os.path.join(adir, "portrait.png")

    if from_file:
        if not os.path.isfile(from_file):
            raise SystemExit("file not found: %s" % from_file)
        import shutil
        shutil.copyfile(from_file, portrait)
        source = "photo:" + os.path.basename(from_file)
    else:
        if generate_prompt:
            prompt = generate_prompt
        else:
            prompt, is_generic = compose_portrait_prompt(gender, language, style, persona)
            if is_generic and not allow_generic:
                raise SystemExit(
                    "EMPTY_PERSONA: gender/style/persona 均为空，会生成完全通用的"
                    "「专业商业人像」形象（无任何客户定制信息）。这类形象和真实客户"
                    "数字人卖点(真人出镜差异化)相悖，容易生成千人一面的形象。请先补充"
                    "职业/年龄/发型/样貌/妆容/气质/性别/风格 中至少一项，或显式传"
                    "--allow-generic 明确接受通用形象（如仅做占位/测试用）。")
        api_key = api_key or key_setup.load_key()
        if not api_key:
            raise SystemExit(ux.friendly_error("No API key. Run key onboarding first."))
        task_id = br_client.create_image_generation(
            api_key, prompt, model="seedream-5.0",
            count=1, resolution="2k", ratio="9:16")
        print("[digital-human] image task submitted: %s" % task_id, flush=True)
        urls = br_client.wait_image_generation(api_key, task_id, interval=5, max_wait=900)
        if not urls:
            raise SystemExit("image generation returned no URL")
        br_client.download(urls[0], portrait, allow_nonpublic_peer=True)
        source = "generated"

    meta = {"name": actor, "client": client, "gender": gender,
            "style": style, "language": language,
            "persona": {k: persona.get(k) for k in PERSONA_FIELDS if persona.get(k)},
            "default_outfit": None, "source": source, "status": "pending"}
    with open(os.path.join(adir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return {"portrait": portrait, "meta": meta}


def confirm_actor(client, actor):
    """Explicitly approve an actor portrait for formal rendering."""
    adir = _actor_dir(client, actor)
    meta_path = os.path.join(adir, "meta.json")
    portrait = os.path.join(adir, "portrait.png")
    if not os.path.isfile(meta_path) or not os.path.isfile(portrait):
        raise SystemExit("actor not found or portrait missing: %s/%s" % (client, actor))
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    meta["status"] = "confirmed"
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    return {"client": client, "actor": actor, "status": "confirmed",
            "portrait": portrait}


def resolve(client, actor, outfit=None, allow_draft=False):
    """Return approved actor refs, or explicitly allow a draft/pending actor."""
    adir = _actor_dir(client, actor)
    meta_path = os.path.join(adir, "meta.json")
    if not os.path.isfile(meta_path):
        raise SystemExit("actor not found: %s/%s" % (client, actor))
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    status = meta.get("status", "unknown")
    if status != "confirmed" and not allow_draft:
        raise SystemExit(
            "UNCONFIRMED_ACTOR: %s/%s status=%s; run confirm first or use "
            "--allow-draft for an explicit preview." % (client, actor, status))
    outfit = outfit or meta.get("default_outfit")
    if outfit:
        base = os.path.join(adir, "outfits", outfit)
        portrait = os.path.join(base, "portrait.png")
    else:
        base = adir
        portrait = os.path.join(adir, "portrait.png")
    refs_dir = os.path.join(base, "refs")
    refs = []
    if os.path.isdir(refs_dir):
        refs = [os.path.join(refs_dir, f) for f in sorted(os.listdir(refs_dir))]
    if not os.path.isfile(portrait):
        raise SystemExit("actor portrait not found: %s" % portrait)
    return {"client": client, "actor": actor, "outfit": outfit,
            "portrait": portrait, "refs": refs,
            "status": status, "draft": status != "confirmed",
            "persona": meta.get("persona", {}),
            "voice_type": meta.get("persona", {}).get("voice_type"),
            "expression": meta.get("persona", {}).get("expression"),
            "body_language": meta.get("persona", {}).get("body_language"),
            "language": meta.get("language", ""),
            "video_type": 5 if refs else 4}


def main(argv):
    p = argparse.ArgumentParser(description="digital-human actor library")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list")
    pl.add_argument("--client", default=None)

    pc = sub.add_parser("create")
    pc.add_argument("--client", required=True)
    pc.add_argument("--actor", required=True)
    pc.add_argument("--gender", default="")
    pc.add_argument("--style", default="")
    pc.add_argument("--language", default="")
    pc.add_argument("--generate", dest="generate_prompt", default=None)
    pc.add_argument("--from-file", dest="from_file", default=None)
    pc.add_argument("--persona", default=None,
                    help="JSON persona: 性格/职业/年龄/发型/样貌/妆容/声音类型/表情/表演基调"
                         " (personality/profession/age/hairstyle/appearance/makeup/voice_type/expression/body_language)")
    pc.add_argument("--allow-generic", action="store_true",
                    help="gender/style/persona 均为空时默认拒绝生成(EMPTY_PERSONA)，"
                         "显式加此项才放行生成完全通用的形象")

    pr = sub.add_parser("resolve")
    pr.add_argument("--client", required=True)
    pr.add_argument("--actor", required=True)
    pr.add_argument("--outfit", default=None)
    pr.add_argument("--allow-draft", action="store_true",
                    help="显式允许 pending/旧版无状态形象用于草稿预览")

    pconfirm = sub.add_parser("confirm")
    pconfirm.add_argument("--client", required=True)
    pconfirm.add_argument("--actor", required=True)

    args = p.parse_args(argv)
    if args.cmd == "list":
        print(json.dumps(list_actors(args.client), ensure_ascii=False, indent=2))
    elif args.cmd == "create":
        persona = json.loads(args.persona) if args.persona else None
        res = create_actor(args.client, args.actor, gender=args.gender,
                           style=args.style, language=args.language,
                           generate_prompt=args.generate_prompt,
                           from_file=args.from_file, persona=persona,
                           allow_generic=args.allow_generic)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "resolve":
        print(json.dumps(resolve(args.client, args.actor, args.outfit,
                                 allow_draft=args.allow_draft),
                          ensure_ascii=False, indent=2))
    elif args.cmd == "confirm":
        print(json.dumps(confirm_actor(args.client, args.actor),
                         ensure_ascii=False, indent=2))
    else:
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
