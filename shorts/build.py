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

# 자막용으로는 굵은 웨이트가 가독성이 좋다. 앞에 올수록 우선 사용된다.
PREFERRED_WEIGHTS = ["ExtraBold", "Bold", "SemiBold"]

FONT_CANDIDATES = {
    "Darwin": ["/Library/Fonts/Pretendard-ExtraBold.otf",
               "/Library/Fonts/Pretendard-Bold.otf",
               str(Path.home() / "Library/Fonts/Pretendard-ExtraBold.otf"),
               str(Path.home() / "Library/Fonts/Pretendard-Bold.otf"),
               "/Library/Fonts/NanumGothicBold.ttf",
               "/System/Library/Fonts/Supplemental/AppleGothic.ttf"],
    "Windows": ["C:/Windows/Fonts/Pretendard-ExtraBold.otf",
                "C:/Windows/Fonts/Pretendard-Bold.otf",
                str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts/Pretendard-ExtraBold.otf"),
                str(Path.home() / "AppData/Local/Microsoft/Windows/Fonts/Pretendard-Bold.otf"),
                "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"],
    "Linux": ["/usr/share/fonts/opentype/pretendard/Pretendard-ExtraBold.otf",
              "/usr/share/fonts/truetype/pretendard/Pretendard-Bold.otf",
              "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf",
              "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
              "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"],
}


def bundled_fonts(base):
    """프로젝트 fonts/ 폴더에 넣어둔 폰트. 설치 없이 그냥 넣으면 쓰인다."""
    d = base / "fonts"
    if not d.is_dir():
        return []
    found = sorted(f for f in d.iterdir()
                   if f.suffix.lower() in (".ttf", ".otf", ".ttc"))
    # 굵은 웨이트를 먼저 쓴다
    def rank(f):
        for i, w in enumerate(PREFERRED_WEIGHTS):
            if w.lower() in f.stem.lower():
                return i
        return len(PREFERRED_WEIGHTS)
    return [str(f) for f in sorted(found, key=rank)]


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


def resolve_font(cfg_font, base=None):
    """쓸 폰트를 정한다: 설정값 → 프로젝트 fonts/ 폴더 → 시스템 설치 폰트."""
    base = base or Path(__file__).resolve().parent
    if cfg_font:
        p = Path(cfg_font).expanduser()
        if not p.is_absolute() and not p.exists():
            p = base / cfg_font          # fonts/Pretendard-Bold.otf 처럼 상대 경로도 허용
        if not p.exists():
            raise BuildError(f"지정한 폰트를 찾을 수 없습니다: {cfg_font}")
        return str(p.resolve())
    for c in bundled_fonts(base) + FONT_CANDIDATES.get(platform.system(),
                                                       FONT_CANDIDATES["Linux"]):
        if Path(c).exists():
            return c
    raise BuildError(
        "한글 폰트를 찾지 못했습니다. 다음 중 하나를 하세요.\n"
        f"  1) 폰트 파일(.otf/.ttf)을 {base / 'fonts'} 폴더에 넣기\n"
        "     Pretendard 추천: https://github.com/orioncactus/pretendard/releases\n"
        "  2) 설정 파일에 경로 지정 — font: C:/Windows/Fonts/malgunbd.ttf")


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
            # expansion=none 이 없으면 '10% 할인' 같은 자막에서 ffmpeg 가
            # % 를 서식 문자로 보고 그 자막을 통째로 그리지 않는다.
            # 오류로 끝나지 않고 조용히 사라지므로 반드시 필요하다.
            f"drawtext=fontfile='{ff_path(font)}':textfile='{ff_path(tf)}':expansion=none"
            f":fontcolor=white:fontsize={opt['size']}:borderw=5:bordercolor=black@0.6"
            f":x=(w-text_w)/2:y=h*{opt['y']}"
            f":alpha='{alpha_expr(start, end, opt['fade'])}'")
    return out


def shine_filter(eff, w, h, fps):
    """화면을 대각선으로 훑고 지나가는 빛줄기를 만든다.

    저해상도로 그린 뒤 키운다 — 부드러운 그라데이션이라 화질 손해가 없고
    픽셀마다 식을 계산하는 비용이 크게 줄어든다.
    """
    ts, te = float(eff["at"][0]), float(eff["at"][1])
    if te <= ts:
        raise BuildError(f"효과 시간 범위가 잘못되었습니다: {eff['at']}")
    k = float(eff.get("angle", 0.7))          # 기울기
    width = float(eff.get("width", 34))       # 빛줄기 두께 (작을수록 가늘다)
    r, g, b = eff.get("rgb", [0.92, 0.74, 0.30])   # 금색
    gain = float(eff.get("intensity", 1.0))
    band = (f"exp(-pow((X+{k}*Y-(-350+((T-{ts})/({te}-{ts}))*(W+{k}*H+700)))/{width},2))")
    gate = f"between(T,{ts},{te})"
    geq = ":".join(f"{c}='255*{v*gain:.3f}*{band}*{gate}'"
                   for c, v in (("r", r), ("g", g), ("b", b)))
    return (f"color=c=black:s={max(160, w//4)}x{max(284, h//4)}:r={fps}:d={eff['_dur']:.2f},"
            f"format=gbrp,geq={geq},scale={w}:{h}")


def normalize_clip(src, dst, cfg, font, captions, tmpdir, idx, trim=None, effects=None):
    """세로 규격 통일 + 구간 자르기 + 자막 굽기 + 오디오 정규화 + 메타데이터 제거."""
    w, h = cfg["size"]
    chain = [f"scale={w}:{h}:force_original_aspect_ratio=increase",
             f"crop={w}:{h}", f"fps={cfg['fps']}", "format=yuv420p"]
    chain += caption_filters(captions, tmpdir, font, idx)
    seek = []
    if trim:
        start, end = float(trim[0]), float(trim[1])
        if end <= start:
            raise BuildError(f"trim 구간이 잘못되었습니다: {trim}")
        seek = ["-ss", f"{start}", "-t", f"{end - start}"]
    aud = "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
    common = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(cfg["fps"]),
              "-c:a", "aac", "-b:a", "192k", "-ac", "2",
              "-map_metadata", "-1"]     # GPS·기기 정보 제거

    if not effects:
        run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src),
             "-vf", ",".join(chain), "-af", aud, *common, str(dst)])
        return

    w, h = cfg["size"]
    dur = (float(trim[1]) - float(trim[0])) if trim else duration_of(src)
    # 합성은 반드시 RGB 에서 한다. YUV 상태로 screen 블렌드를 하면
    # 색차 성분까지 섞여 화면 전체 색이 틀어진다.
    fc = ["[0:v]" + ",".join(chain) + ",format=gbrp[b0]"]
    for j, eff in enumerate(effects):
        if eff.get("type", "shine") != "shine":
            raise BuildError(f"알 수 없는 효과: {eff.get('type')}")
        eff = {**eff, "_dur": dur + 0.2}
        fc.append(f"{shine_filter(eff, w, h, cfg['fps'])}[s{j}]")
        fc.append(f"[b{j}][s{j}]blend=all_mode=screen:shortest=1[b{j+1}]")
    fc[-1] = fc[-1].replace(f"[b{len(effects)}]", ",format=yuv420p[v]")
    run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src),
         "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "0:a",
         "-af", aud, *common, str(dst)])


HARD_CUT = 0.06     # 이보다 짧은 전환은 겹치지 않고 그냥 이어붙인다


def _join_fast(parts, dst, tmpdir):
    """같은 규격의 파일들을 다시 인코딩하지 않고 이어붙인다.

    겹침(xfade)은 앞의 결과물을 계속 다시 처리하므로 클립이 늘수록
    급격히 느려진다. 하드컷 구간은 이 방식으로 처리해 그 비용을 없앤다.
    """
    lst = Path(tmpdir) / f"{dst.stem}_list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", "-map_metadata", "-1", str(dst)])
    return dst


def concat(parts, dst, cfg, xfades=None, tmpdir=None):
    """크로스페이드로 이어붙이고 앞뒤에 페이드를 넣는다.

    xfades[i] 는 parts[i] 와 parts[i+1] 사이의 전환 길이. 클립마다 다르게
    줄 수 있어, 같은 장면을 빠르게 반복하는 연출도 만들 수 있다.
    """
    durs = [duration_of(p) for p in parts]
    n = len(parts)
    if xfades is None:
        xfades = [cfg["transition"]] * (n - 1)

    for i, d in enumerate(durs):
        inc = xfades[i - 1] if i > 0 else 0.0
        out = xfades[i] if i < n - 1 else 0.0
        if d <= max(inc, out) + 0.05:
            raise BuildError(
                f"{parts[i].name} 이 맞닿은 전환보다 짧습니다 "
                f"(길이 {d:.2f}초, 전환 {max(inc, out):.2f}초). "
                f"구간을 늘리거나 transition 을 줄이세요.")

    # 하드컷으로 이어지는 클립들을 먼저 한 덩어리로 합친다.
    # 겹침 단계 수가 줄어 렌더링 시간이 크게 짧아진다.
    if tmpdir is not None and any(x < HARD_CUT for x in xfades):
        groups, cur = [], [0]
        for i in range(1, n):
            if xfades[i - 1] < HARD_CUT:
                cur.append(i)          # 앞 클립과 하드컷 → 같은 덩어리
            else:
                groups.append(cur)     # 겹침이 필요 → 덩어리를 끊는다
                cur = [i]
        groups.append(cur)
        merged, new_xf = [], []
        for k, g in enumerate(groups):
            merged.append(parts[g[0]] if len(g) == 1
                          else _join_fast([parts[j] for j in g],
                                          Path(tmpdir) / f"seg{k}.mp4", tmpdir))
            if k < len(groups) - 1:
                new_xf.append(xfades[g[-1]])
        if len(merged) < n:
            return concat(merged, dst, cfg, new_xf, None)

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]

    vg, ag, running = [], [], durs[0]
    vprev, aprev = "0:v", "0:a"
    for i in range(1, n):
        xf = xfades[i - 1]
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


def mix_all(video, wav, narr, dst, music, audio, narration, total):
    """나레이션이 있으면 그것을 중심에 두고 BGM·현장음을 눌러준다."""
    nv = float(narration.get("volume", 1.0))
    d = int(float(narration.get("delay", 0.0)) * 1000)
    # 사이드체인 임계값. 낮을수록 세게 눌린다.
    # 실측: 실제 현장음을 키로 0.045 → 약 9~12 dB, 0.02 → 약 15~18 dB 감쇠.
    # 0.1 이상이면 키가 임계값에 못 미쳐 아무 일도 일어나지 않으니 올리지 말 것.
    duck_bgm = float(narration.get("duck_music", 0.03))
    duck_amb = float(narration.get("duck_ambient", 0.05))
    bv = music["volume"] if music.get("style") not in (None, "none") else 0.0
    fo = max(0.0, total - 1.0)

    parts = [f"[2:a]adelay={d}|{d},aformat=fltp:48000:stereo,volume={nv},"
             f"asplit=3[nar][nk1][nk2]"]
    mixes = ["[nar]"]
    if bv > 0:
        parts.append(f"[1:a]aformat=fltp:48000:stereo,volume={bv}[mus]")
        parts.append(f"[mus][nk1]sidechaincompress=threshold={duck_bgm}:ratio=12:"
                     f"attack=15:release=420:makeup=1[musd]")
        mixes.append("[musd]")
    if audio["keep_original"]:
        parts.append("[0:a]aformat=fltp:48000:stereo[voc]")
        parts.append(f"[voc][nk2]sidechaincompress=threshold={duck_amb}:ratio=4:"
                     f"attack=15:release=420:makeup=1[vocd]")
        mixes.append("[vocd]")
    parts.append(f"{''.join(mixes)}amix=inputs={len(mixes)}:duration=first:"
                 f"dropout_transition=0,afade=t=out:st={fo:.3f}:d=0.9,"
                 f"loudnorm=I={audio['loudness']}:TP=-1.5:LRA=11,aresample=48000[a]")

    ins = ["-i", str(video)]
    ins += ["-i", str(wav)] if bv > 0 else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    ins += ["-i", str(narr)]
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-movflags", "+faststart", "-map_metadata", "-1", str(dst)])


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
    merged["narration"] = cfg.get("narration") or None
    merged["clips"] = cfg["clips"]
    merged["base"] = Path(path).resolve().parent
    return merged


def build(cfg_path, outdir):
    cfg = load_config(cfg_path)
    font = resolve_font(cfg["font"], cfg["base"])
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    name = cfg["output"]

    with tempfile.TemporaryDirectory() as tmp:
        parts, cache = [], {}
        for i, clip in enumerate(cfg["clips"]):
            src = (cfg["base"] / clip["file"]).expanduser()
            if not src.exists():
                raise BuildError(f"영상 파일을 찾을 수 없습니다: {src}")
            # 내용이 같은 조각은 한 번만 만들어 재사용한다.
            # 같은 장면을 여러 번 반복하는 연출에서 인코딩 횟수가 크게 준다.
            key = json.dumps([clip["file"], clip.get("trim"), clip.get("captions"),
                              clip.get("effects")], ensure_ascii=False, sort_keys=True)
            if key in cache:
                parts.append(cache[key])
                continue
            dst = Path(tmp) / f"part{i}.mp4"
            print(f"  [{i+1}/{len(cfg['clips'])}] {src.name} 처리 중...")
            normalize_clip(src, dst, cfg, font, clip.get("captions") or [], tmp, i,
                           clip.get("trim"), clip.get("effects"))
            cache[key] = dst
            parts.append(dst)

        print("  클립 연결 중...")
        nomusic = outdir / f"{name}_nomusic.mp4"
        # 클립마다 transition 을 따로 줄 수 있다 (앞 클립과의 전환 길이)
        xfades = [float(c.get("transition", cfg["transition"])) for c in cfg["clips"][1:]]
        total = concat(parts, nomusic, cfg, xfades, tmp)

        final = outdir / f"{name}.mp4"
        narr = None
        if cfg["narration"]:
            narr = (cfg["base"] / cfg["narration"]["file"]).expanduser()
            if not narr.exists():
                raise BuildError(f"나레이션 파일을 찾을 수 없습니다: {narr}")

        if narr:
            wav = Path(tmp) / "bgm.wav"
            if cfg["music"]["style"] not in (None, "none"):
                print(f"  BGM 생성 중 ({cfg['music']['style']})...")
                bgm_mod.write_wav(
                    bgm_mod.generate(cfg["music"]["style"], total, cfg["music"]["bpm"]), wav)
            print("  나레이션 믹싱 중...")
            mix_all(nomusic, wav, narr, final, cfg["music"], cfg["audio"],
                    cfg["narration"], total)
        elif cfg["music"]["style"] in (None, "none"):
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
