#!/usr/bin/env python3
"""숏폼 렌더러 대시보드.

브라우저에서 클립을 고르고 자막·음악을 맞춘 뒤 바로 렌더링한다.

    python app.py            # http://127.0.0.1:8765 자동 실행
    python app.py --port 9000 --no-browser
"""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_file, send_from_directory

import bgm as bgm_mod
import build as build_mod

HERE = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(HERE / "static"), static_url_path="")

PROJECT = HERE            # --project 로 변경 가능
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}

_render = {"running": False, "log": [], "done": False, "ok": False, "outputs": []}
_lock = threading.Lock()


# ── 경로 유틸 ────────────────────────────────────────────────────
def clips_dir():
    d = PROJECT / "clips"; d.mkdir(parents=True, exist_ok=True); return d


def cache_dir():
    d = PROJECT / ".cache"; d.mkdir(parents=True, exist_ok=True); return d


def out_dir():
    d = PROJECT / "out"; d.mkdir(parents=True, exist_ok=True); return d


def safe_clip(name):
    """clips/ 바깥을 가리키는 경로를 차단한다."""
    p = (clips_dir() / name).resolve()
    if not str(p).startswith(str(clips_dir().resolve())) or not p.is_file():
        return None
    return p


# ── 프리뷰 프록시 ────────────────────────────────────────────────
def proxy_path(src):
    return cache_dir() / f"{src.stem}_{int(src.stat().st_mtime)}_proxy.mp4"


def thumb_path(src):
    return cache_dir() / f"{src.stem}_{int(src.stat().st_mtime)}_thumb.jpg"


def _cached(dst, args):
    """ffmpeg 결과를 캐시한다.

    임시 파일에 쓴 뒤 옮긴다 — 중간에 실패하거나 강제 종료되어도
    0바이트 파일이 캐시에 남아 계속 재사용되는 일이 없게 한다.
    """
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    # 확장자를 유지해야 ffmpeg 가 컨테이너 형식을 알아낸다
    tmp = dst.with_name(f".{dst.stem}.part{dst.suffix}")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(tmp)],
                       capture_output=True, check=True)
        if tmp.stat().st_size == 0:
            raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"empty output")
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst


def make_proxy(src):
    """브라우저가 확실히 재생할 수 있는 저용량 H.264 사본을 만든다.

    아이폰 원본은 HEVC라 크롬에서 재생이 안 되는 경우가 있다.
    """
    return _cached(proxy_path(src), [
        "-i", str(src), "-vf", "scale=-2:720",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
        "-map_metadata", "-1"])


def make_thumb(src):
    return _cached(thumb_path(src), [
        "-ss", "0.5", "-i", str(src), "-frames:v", "1",
        "-vf", "scale=-2:320", "-q:v", "4", "-map_metadata", "-1"])


# ── API: 클립 ────────────────────────────────────────────────────
@app.get("/api/clips")
def api_clips():
    items = []
    for p in sorted(clips_dir().iterdir()):
        if p.suffix.lower() not in VIDEO_EXT:
            continue
        try:
            dur = build_mod.duration_of(p)
        except Exception:
            continue
        items.append({"name": p.name, "duration": round(dur, 2),
                      "size_mb": round(p.stat().st_size / 1_048_576, 1),
                      "ready": proxy_path(p).exists()})
    return jsonify(items)


@app.post("/api/upload")
def api_upload():
    saved = []
    for f in request.files.getlist("files"):
        name = Path(f.filename).name
        if not name or Path(name).suffix.lower() not in VIDEO_EXT:
            continue
        dst = clips_dir() / name
        stem, suf, i = dst.stem, dst.suffix, 1
        while dst.exists():
            dst = clips_dir() / f"{stem}_{i}{suf}"; i += 1
        f.save(dst); saved.append(dst.name)
    return jsonify({"saved": saved})


@app.get("/api/thumb/<path:name>")
def api_thumb(name):
    p = safe_clip(name)
    if not p:
        return "", 404
    try:
        return send_file(make_thumb(p), mimetype="image/jpeg")
    except subprocess.CalledProcessError:
        return "", 500


@app.get("/api/video/<path:name>")
def api_video(name):
    p = safe_clip(name)
    if not p:
        return "", 404
    try:
        return send_file(make_proxy(p), mimetype="video/mp4", conditional=True)
    except subprocess.CalledProcessError:
        return "", 500


@app.post("/api/prepare")
def api_prepare():
    """선택한 클립들의 프리뷰 사본을 미리 만들어 둔다."""
    names = request.json.get("names", [])
    done = []
    for n in names:
        p = safe_clip(n)
        if p:
            try:
                make_proxy(p); make_thumb(p); done.append(n)
            except subprocess.CalledProcessError:
                pass
    return jsonify({"prepared": done})


# ── API: BGM 미리듣기 ────────────────────────────────────────────
@app.get("/api/bgm/styles")
def api_bgm_styles():
    return jsonify([{"id": k, "bpm": v["bpm"], "desc": v["desc"]}
                    for k, v in bgm_mod.PRESETS.items()])


@app.get("/api/bgm/preview")
def api_bgm_preview():
    style = request.args.get("style", "cheerful")
    if style not in bgm_mod.PRESETS:
        return "", 404
    dst = cache_dir() / f"preview_{style}.wav"
    if not dst.exists():
        bgm_mod.write_wav(bgm_mod.generate(style, 10.0), dst)
    return send_file(dst, mimetype="audio/wav", conditional=True)


# ── API: 설정 ────────────────────────────────────────────────────
CONFIG = lambda: PROJECT / "config.yaml"


@app.get("/api/config")
def api_config_get():
    if CONFIG().exists():
        return jsonify(yaml.safe_load(CONFIG().read_text(encoding="utf-8")) or {})
    return jsonify({})


@app.post("/api/config")
def api_config_save():
    cfg = request.json or {}
    CONFIG().write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")
    return jsonify({"saved": str(CONFIG())})


# ── API: 렌더링 ──────────────────────────────────────────────────
def _run_render(cfg):
    with _lock:
        _render.update(running=True, log=[], done=False, ok=False, outputs=[])
    try:
        CONFIG().write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200),
            encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(HERE / "build.py"), str(CONFIG()),
             "-o", str(out_dir())],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                with _lock:
                    _render["log"].append(line)
        rc = proc.wait()
        name = cfg.get("output", "output")
        outs = [f"{name}.mp4", f"{name}_nomusic.mp4", f"{name}_thumb.jpg"]
        with _lock:
            _render["ok"] = (rc == 0)
            _render["outputs"] = [o for o in outs if (out_dir() / o).exists()] if rc == 0 else []
    except Exception as e:
        with _lock:
            _render["log"].append(f"오류: {e}")
            _render["ok"] = False
    finally:
        with _lock:
            _render.update(running=False, done=True)


@app.post("/api/render")
def api_render():
    with _lock:
        if _render["running"]:
            return jsonify({"error": "이미 렌더링 중입니다"}), 409
    cfg = request.json or {}
    if not cfg.get("clips"):
        return jsonify({"error": "클립을 하나 이상 선택하세요"}), 400
    threading.Thread(target=_run_render, args=(cfg,), daemon=True).start()
    return jsonify({"started": True})


@app.get("/api/render/status")
def api_render_status():
    with _lock:
        return jsonify(dict(_render))


@app.get("/api/out/<path:name>")
def api_out(name):
    p = (out_dir() / name).resolve()
    if not str(p).startswith(str(out_dir().resolve())) or not p.is_file():
        return "", 404
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "video/mp4"
    return send_file(p, mimetype=mime, conditional=True)


@app.post("/api/reveal")
def api_reveal():
    """산출물 폴더를 탐색기/파인더로 연다."""
    target = str(out_dir())
    try:
        s = platform.system()
        if s == "Darwin":
            subprocess.Popen(["open", target])
        elif s == "Windows":
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return jsonify({"opened": target})
    except Exception as e:
        return jsonify({"error": str(e), "path": target}), 500


@app.get("/api/env")
def api_env():
    """시작 시 환경 점검 결과."""
    font, font_err = None, None
    try:
        font = build_mod.resolve_font(None)
    except build_mod.BuildError as e:
        font_err = str(e)
    return jsonify({
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "font": font, "font_error": font_err,
        "project": str(PROJECT), "out": str(out_dir()),
        "clips_dir": str(clips_dir()),
    })


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def main():
    global PROJECT
    ap = argparse.ArgumentParser(description="숏폼 렌더러 대시보드")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--project", default=None, help="작업 폴더 (기본: app.py 위치)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.project:
        PROJECT = Path(a.project).expanduser().resolve()
    if shutil.which("ffmpeg") is None:
        print("경고: ffmpeg 를 찾을 수 없습니다. 렌더링이 실패합니다.", file=sys.stderr)
    url = f"http://127.0.0.1:{a.port}"
    print(f"\n  대시보드: {url}")
    print(f"  작업 폴더: {PROJECT}")
    print(f"  영상 넣는 곳: {clips_dir()}\n")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=a.port, threaded=True)


if __name__ == "__main__":
    main()
