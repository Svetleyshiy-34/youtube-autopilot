#!/usr/bin/env python3
"""ai-channel: automatic short educational videos on any topic.


One run = one video:
    topic -> script (LLM) -> slides (Pillow) -> voise (TTS) -> video (ffmpeg) -> YouTube (optional)

All setings live in config.toml, so the same code works for any niche,
language, model and voice.

Usage:
    python channel.py                 # uses ./config.toml
    python channel.py my_config.toml  # another config
    python channel.py --no-upload     #build the video only
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap
import tomllib
import urllib.request
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_SANS_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT_MONO_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejavuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
]

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_root"] = path.resolve().parent
    return cfg

def resolve(cfg: dict, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else cfg["_root"] / path

def pick_topic(cfg: dict) -> str:
    topics_file = resolve(cfg, cfg["channel"]["topics_file"])
    done_file = resolve(cfg, cfg["channel"]["done_file"])
    done = set(done_file.read_text(encoding="utf-8").splitlines()) if done_file.exists() else set()
    for line in topics_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in done:
            return line
        sys.exit("No topics left: add new lines to" + str(topics_file))

def mark_done(cfg: dict, topic: str) -> None:
    done_file = resolve(cfg, cfg["channel"]["done_file"])
    with done_file.open("a", encoding="utf-8") as f:
        f.write(topic + "\n")

def build_prompt(cfg: dict, topic: str) -> str:
    c = cfg["channel"]
    n = c["slides_count"]
    return f"""You write short educational videos for a YouTube channel.
Channel niche: {c['niche']}
Audience: {c['audience']}
Style: {c['style']}
Write ALL text values in this language: {c['language']}.
Topic of this video: "{topic}".

Return ONLY valid JSON, no explanations, in this exact shape:
{{"title:" "catchy title, up to 80 characters",
  "description": "video description, 2-3 sentences",
  "tags": ["tag1", "tag2"],
  "slides": [
  {{"heading": "short slide heading",
    "visual": "optional text block shown on the slide (code, formula, list), up to 8 short lines, may be empty",
    "narration": "what the narrator says 1-3 simple sentences"}}
    ]}}
Make exactly {n} slides: an introduction, the main steps, and a short summary"""


def call_llm(cfg: dict, prompt: str) -> str:
    llm = cfg["llm"]
    base = llm["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key_env"):
        headers["Autorization"] = "Bearer " + os.environ[llm["api_key_env"]]

    if llm["provider"] == "ollama":
        url = base + "/api/generate"
        payload = {
            "model": llm["model"], "prompt": prompt, "stream": False,
            "format": "json",
            "keep_alive" : 0,
            "options": {"num_ctx": llm.get("num_ctx", 2048), "temperature": llm["temperature"]},
        }
        extract = lambda r: r["response"]
    elif llm["provider"] == "openai":
        url = base + "/chat/completions"
        payload = {
            "model": llm["model"], "temperature": llm["temperature"],
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        extract = lambda r: r["choises"][0]["message"]["content"]
    else:
        sys.exit("llm.provider must be 'ollama' or 'openai'")

    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=1800) as r:
        return extract(json.loads(r.read()))


def parse_json(text: str) -> dict:
    return json.loads(text[text.index("{"): text.rindex("}") + 1])

def generate_script(cfg: dict, topic: str) -> dict:
    prompt = build_prompt(cfg, topic)
    for attempt in range(3):
        try:
            data = parse_json(call_llm(cfg, prompt))
            assert data["title"] and len(data["slides"]) >= 2
            for s in data["slides"]:
                assert s["heading"] and s["narration"]
                s["visual"] = s.get("visual", "") or ""
            data.setdefault("description", topic)
            data.setdefault("tags", [])
            return data
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
    sys.exit("The model could not produce a valid script.Try another model in config.toml.")


def load_font(configured: str, fallbacks: list, size: int):
    for p in ([configured] if configured else []) + fallbacks:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
        return ImageFont.load_default()


def render_slide(cfg: dict, slide: dict, path: Path) -> None:
    v = cfg["video"]
    w,h = v["width"], v["height"]
    img = Image.new("RGB", (w, h), tuple(v["bg_color"]))
    d = ImageDraw.Draw(img)

    d.text((60, 50), slide["heading"],
           font=load_font(v["font_sans"], FONT_SANS_FALLBACKS, 52), fill=tuple(v["title_color"]))

    text = slide["visual"].strip()
    if text:
        d.rounded_rectangle((60, 170, w - 60, h - 60), 16, fill=tuple(v["box_color"]))

        wrapped_lines = []
        for line in text.split('\n'):
            wrapped_lines.extend(textwrap.wrap(line, width=60))

        wrapped_text = "\n".join(wrapped_lines)

        d.multiline_text((90, 200), wrapped_text,
                         font=load_font(v["font_mono"], FONT_SANS_FALLBACKS, 34),
                         fill=tuple(v["text_color"]), spacing=10)
    img.save(path)


def tts(cfg: dict,text: str, wav: Path ) -> None:
    t = cfg["tts"]
    if t["engine"] == "piper":
        subprocess.run(
            ["piper", "--model", str(resolve(cfg, t["voice"])), "--output_file", str(wav)],
            input=text.encode(), check=True, stdout=subprocess.DEVNULL,
        )
    elif t["engine"] == "espeak":
        subprocess.run(["espeak-ng", "-v", t["voice"], "-w", str(wav), text], check=True)
    else:
        sys.exit("tts.engine must be 'piper' or 'espeak'")

def build_clip(png: Path, wav: Path, mp4: Path) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "10",
        "-i", str(png), "-i", str(wav),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "shortest", str(mp4),
    ], check=True)

def concat(clips: list, final:Path) -> None:
    listing = final.parent / "clips.txt"
    listing.write_text("". join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    subprocess.run([
        "ffmeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", str(final),
    ], check=True)

def youtube_client(cfg: dict):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    token = resolve(cfg, "token.json")
    creds = Credentials.from_authorized_user_file(str(token), scopes) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(resolve(cfg, "client_secret.json")), scopes)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def upload(cfg: dict, video: Path, script: dict) -> None:
    from googleapiclient.http import MediaFileUpload

    yt = cfg["youtube"]
    description = script["description"]
    if cfg["channel"].get("signature"):
        description += "\n\n" + cfg["channel"]["signature"]
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": description,
            "tags": script["tags"],
            "categoryId": str(yt["category_id"]),
        },
        "status": {"privacyStatus": yt["privacy"], "selfDeclaredMadeForKids": False},
    }
    req = youtube_client(cfg).videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(video), resumable=True),
    )
    resp = None
    while resp is None:
        ap = argparse.ArgumentParser(description="Automatic YouTube video pipeline")
        ap.add_argument("config", nargs="?", default="config.toml")
        ap.add_argument("--no-upload", action="store_true", help="build the video, do not upload")
        args = ap.parse_args()

        cfg = load_config(Path(args.config))
        topic = pick_topic(cfg)
        print("Topic:", topic)    

        work = resolve(cfg, cfg["channel"]["output_dir"]) / "current"
        work.mkdir(parents=True, exist_ok=True)

        script = generate_script(cfg, topic)
        (work / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

        clips = []
        for i, slide in enumerate(script["slides"]):
            png, wav, mp4 = work / f"{i}.png", work / f"{i}.wav",  work / f"{i}.mp4"
            render_slide(cfg, slide, png)
            tts(cfg, slide["narration"], wav)
            build_clip(png, wav, mp4)
            clips.append(mp4)

        final = work / "video.mp4"
        concat(clips, final)
        print("Video ready:", final)

        if cfg["youtube"]["upload"] and not args.no_upload:
            upload(cfg, final, script)
        mark_done(cfg, topic)

if __name__ == "__main__":
    main()
