#!/usr/bin/env python3
"""숏폼 영상 자동 렌더러.

설정 파일(YAML) 하나로 클립 연결·자막·BGM·메타데이터 제거까지 한 번에 처리한다.

    python build.py config.yaml

산출물:
    <output>.mp4          BGM 포함 (업로드용)
    <output>_nomusic.mp4  BGM 없음 (앱 내장 음원용)
    <output>_thumb.jpg    썸네일
"""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

import bgm as bgm_mod

# ── 기본값 ───────────────────────────────────────────────────────
DEFAULTS = {
    "size": [1080, 1920],
    "fps": 30,
    "transition": 0.4,      # 클립 사이 크로스페이드(초)
    "fade_in": 0.5,
    "fade_out": 0.5,
    "thumbnail_at": None,   # None이면 전체 길이의 55% 지점
    "crf": 23,
    "maxrate": "6M",
}
CAPTION_DEFAULTS = {"y": 0.80, "size": 56, "fade": 0.4}
MUSIC_DEFAULTS = {"style": "cheerful", "bpm": None, "volume": 0.42, "duck": True}
AUDIO_DEFAULTS = {"keep_original": True, "loudness": -14}

FONT_CANDIDATES = {
    "Darwin": ["/System/Library/Fonts/Supplemental/AppleGothic.ttf",
               "/Library/Fonts/NanumGothicBold.ttf",
               "/Library/Fonts/NanumGothic.ttf"],
    "Windows": ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"],
    "Linux": ["/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf",
              "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
              "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"],
}


class BuildError(Exception):
    pass


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-12:])
        raise BuildError(f"ffmpeg 실행 실패:\n  {' '.join(str(c) for c in cmd[:6])} ...\n{tail}")
    return r


def need_tool(name):
    if shutil.which(name) is None:
        raise BuildError(
            f"'{name}' 가 설치되어 있지 않습니다.\n"
            "  macOS : brew install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  Linux : sudo apt install ffmpeg")


def duration_of(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


def resolve_font(cfg_font):
    if cfg_font:
        if not Path(cfg_font).exists():
            raise BuildError(f"지정한 폰트를 찾을 수 없습니다: {cfg_font}")
        return cfg_font
    for c in FONT_CANDIDATES.get(platform.system(), FONT_CANDIDATES["Linux"]):
        if Path(c).exists():
            return c
    raise BuildError(
        "한글 폰트를 찾지 못했습니다. 설정 파일에 font 항목으로 경로를 직접 지정하세요.\n"
        "  예) font: /Library/Fonts/NanumGothicBold.ttf")


def ff_path(p):
    """ffmpeg 필터 인자용 경로 이스케이프."""
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def alpha_expr(start, end, fade):
    """자막이 부드럽게 나타났다 사라지는 alpha 수식."""
    return (f"if(lt(t,{start}),0,"
            f"if(lt(t,{start}+{fade}),(t-{start})/{fade},"
            f"if(lt(t,{end}-{fade}),1,"
            f"if(lt(t,{end}),({end}-t)/{fade},0))))")


def caption_filters(captions, tmpdir, font, idx):
    """자막을 textfile 방식으로 넣는다 — 따옴표·콜론 이스케이프 문제를 피한다."""
    out = []
    for j, c in enumerate(captions):
        opt = {**CAPTION_DEFAULTS, **c}
        if "text" not in opt or "at" not in opt:
            raise BuildError(f"자막 항목에 text 또는 at 이 없습니다: {c}")
        start, end = float(opt["at"][0]), float(opt["at"][1])
        if end <= start:
            raise BuildError(f"자막 시간 범위가 잘못되었습니다: {opt['text']} {opt['at']}")
        tf = Path(tmpdir) / f"cap_{idx}_{j}.txt"
        tf.write_text(str(opt["text"]), encoding="utf-8")
        out.append(
            f"drawtext=fontfile='{ff_path(font)}':textfile='{ff_path(tf)}'"
            f":fontcolor=white:fontsize={opt['size']}:borderw=5:bordercolor=black@0.6"
            f":x=(w-text_w)/2:y=h*{opt['y']}"
            f":alpha='{alpha_expr(start, end, opt['fade'])}'")
    return out


def normalize_clip(src, dst, cfg, font, captions, tmpdir, idx):
    """세로 규격 통일 + 자막 굽기 + 오디오 정규화 + 메타데이터 제거."""
    w, h = cfg["size"]
    chain = [f"scale={w}:{h}:force_original_aspect_ratio=increase",
             f"crop={w}:{h}", f"fps={cfg['fps']}", "format=yuv420p"]
    chain += caption_filters(captions, tmpdir, font, idx)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-vf", ",".join(chain),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(cfg["fps"]),
         "-c:a", "aac", "-b:a", "192k", "-ac", "2",
         "-map_metadata", "-1",          # GPS·기기 정보 제거
         str(dst)])


def concat(parts, dst, cfg):
    """크로스페이드로 이어붙이고 앞뒤에 페이드를 넣는다."""
    durs = [duration_of(p) for p in parts]
    xf = cfg["transition"]
    for i, d in enumerate(durs):
        if d <= xf + 0.1:
            raise BuildError(f"{parts[i].name} 이 전환 길이보다 짧습니다 ({d:.2f}초)")

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    vg, ag, running = [], [], durs[0]
    vprev, aprev = "0:v", "0:a"
    for i in range(1, len(parts)):
        offset = running - xf
        vg.append(f"[{vprev}][{i}:v]xfade=transition=fade:duration={xf}:offset={offset:.3f}[v{i}]")
        ag.append(f"[{aprev}][{i}:a]acrossfade=d={xf}[a{i}]")
        vprev, aprev = f"v{i}", f"a{i}"
        running += durs[i] - xf

    total = running
    fo_start = max(0.0, total - cfg["fade_out"])
    vg.append(f"[{vprev}]fade=t=in:st=0:d={cfg['fade_in']},"
              f"fade=t=out:st={fo_start:.3f}:d={cfg['fade_out']},format=yuv420p[v]")
    graph = ";".join(vg + ag)
    amap = f"[{aprev}]" if len(parts) > 1 else "0:a"

    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph,
         "-map", "[v]", "-map", amap,
         "-c:v", "libx264", "-preset", "slow", "-crf", str(cfg["crf"]),
         "-maxrate", cfg["maxrate"], "-bufsize", "12M",
         "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", "-r", str(cfg["fps"]),
         "-map_metadata", "-1", str(dst)])
    return total


def mix_music(video, wav, dst, music, audio, total):
    """BGM을 아래에 깔고, 현장음이 커지면 자동으로 눌러준다(사이드체인 더킹)."""
    vol = music["volume"]
    fo = max(0.0, total - 1.0)
    if audio["keep_original"]:
        if music["duck"]:
            chain = (f"[0:a]aformat=fltp:48000:stereo,asplit=2[voc][sc];"
                     f"[1:a]aformat=fltp:48000:stereo,volume={vol}[mus];"
                     f"[mus][sc]sidechaincompress=threshold=0.045:ratio=6:"
                     f"attack=12:release=380:makeup=1[duck];"
                     f"[voc][duck]amix=inputs=2:duration=first:dropout_transition=0")
        else:
            chain = (f"[0:a]aformat=fltp:48000:stereo[voc];"
                     f"[1:a]aformat=fltp:48000:stereo,volume={vol}[mus];"
                     f"[voc][mus]amix=inputs=2:duration=first:dropout_transition=0")
    else:
        chain = f"[1:a]aformat=fltp:48000:stereo,volume={vol}"
    chain += (f",afade=t=out:st={fo:.3f}:d=0.9,"
              f"loudnorm=I={audio['loudness']}:TP=-1.5:LRA=11,aresample=48000[a]")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(wav),
         "-filter_complex", chain, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", "-map_metadata", "-1", str(dst)])


def thumbnail(video, dst, at):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", "-map_metadata", "-1", str(dst)])


def load_config(path):
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not cfg.get("clips"):
        raise BuildError("설정 파일에 clips 항목이 없습니다.")
    merged = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
    merged["output"] = cfg.get("output", Path(path).stem)
    merged["font"] = cfg.get("font")
    merged["music"] = {**MUSIC_DEFAULTS, **(cfg.get("music") or {})}
    merged["audio"] = {**AUDIO_DEFAULTS, **(cfg.get("audio") or {})}
    merged["clips"] = cfg["clips"]
    merged["base"] = Path(path).resolve().parent
    return merged


def build(cfg_path, outdir):
    cfg = load_config(cfg_path)
    font = resolve_font(cfg["font"])
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    name = cfg["output"]

    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, clip in enumerate(cfg["clips"]):
            src = (cfg["base"] / clip["file"]).expanduser()
            if not src.exists():
                raise BuildError(f"영상 파일을 찾을 수 없습니다: {src}")
            dst = Path(tmp) / f"part{i}.mp4"
            print(f"  [{i+1}/{len(cfg['clips'])}] {src.name} 처리 중...")
            normalize_clip(src, dst, cfg, font, clip.get("captions") or [], tmp, i)
            parts.append(dst)

        print("  클립 연결 중...")
        nomusic = outdir / f"{name}_nomusic.mp4"
        total = concat(parts, nomusic, cfg)

        final = outdir / f"{name}.mp4"
        if cfg["music"]["style"] in (None, "none"):
            shutil.copy(nomusic, final)
        else:
            print(f"  BGM 생성 중 ({cfg['music']['style']})...")
            wav = Path(tmp) / "bgm.wav"
            bgm_mod.write_wav(
                bgm_mod.generate(cfg["music"]["style"], total, cfg["music"]["bpm"]), wav)
            print("  믹싱 중...")
            mix_music(nomusic, wav, final, cfg["music"], cfg["audio"], total)

        at = cfg["thumbnail_at"] if cfg["thumbnail_at"] is not None else total * 0.55
        thumb = outdir / f"{name}_thumb.jpg"
        thumbnail(final, thumb, at)

    print(f"\n완료 ({total:.1f}초)")
    for f in (final, nomusic, thumb):
        print(f"  {f}  ({f.stat().st_size/1_048_576:.1f} MB)")
    return final


def main():
    ap = argparse.ArgumentParser(description="숏폼 영상 자동 렌더러")
    ap.add_argument("config", help="설정 YAML 경로")
    ap.add_argument("-o", "--outdir", default="out", help="산출물 폴더 (기본: out)")
    a = ap.parse_args()
    try:
        need_tool("ffmpeg"); need_tool("ffprobe")
        build(a.config, a.outdir)
    except BuildError as e:
        print(f"\n오류: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
