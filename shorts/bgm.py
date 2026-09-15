#!/usr/bin/env python3
"""저작권 문제가 없는 오리지널 BGM 생성기.

스타일 프리셋을 골라 지정한 길이의 WAV를 만든다. 외부 음원을 쓰지 않으므로
수익화·저작권 신고 위험이 없다.

    python bgm.py --style cheerful --duration 30 --out bgm.wav
"""
import argparse
import wave

import numpy as np

SR = 48000

# ── 스타일 프리셋 ────────────────────────────────────────────────
# prog: (베이스음, [코드 구성음]) — MIDI 노트 번호
POP_PROG = [(48, [60, 64, 67]), (43, [59, 62, 67]),
            (45, [60, 64, 69]), (41, [57, 60, 65])]   # C - G - Am - F
BALLAD_PROG = [(48, [60, 64, 67]), (45, [60, 64, 69]),
               (41, [57, 60, 65]), (43, [59, 62, 67])]  # C - Am - F - G

PRESETS = {
    "cheerful": dict(bpm=122, prog=POP_PROG, lead="marimba", comp="ukulele",
                     drums=True, swing=0.0, lead_gain=1.00,
                     desc="마림바 멜로디 + 우쿨렐레. 밝고 통통 튀는 육아 브이로그 톤"),
    "calm":     dict(bpm=84,  prog=BALLAD_PROG, lead="bell", comp="pad",
                     drums=False, swing=0.0, lead_gain=0.85,
                     desc="벨 + 패드. 드럼 없이 잔잔한 감성 톤"),
    "playful":  dict(bpm=132, prog=POP_PROG, lead="pluck", comp="ukulele",
                     drums=True, swing=0.12, lead_gain=0.95,
                     desc="피치카토 + 우드블록. 장난스럽고 빠른 톤"),
}

# 멜로디 모티프: (시작 박, 길이 박, 음높이) — C장조 펜타토닉 중심
MOTIFS = [
    [(0, .5, 72), (.5, .5, 76), (1, .5, 79), (1.5, .5, 76), (2, 1, 74), (3, .5, 72), (3.5, .5, 74)],
    [(0, .5, 74), (.5, .5, 72), (1, .5, 69), (1.5, .5, 72), (2, 1.5, 71), (3.5, .5, 67)],
    [(0, .5, 72), (.5, .5, 74), (1, .5, 76), (1.5, .5, 79), (2, 1, 81), (3, 1, 79)],
    [(0, .5, 77), (.5, .5, 76), (1, .5, 74), (1.5, .5, 72), (2, 2, 72)],
]


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12.0)


def _env(n, attack, decay):
    t = np.arange(n) / SR
    return np.exp(-t * decay) * np.minimum(1.0, t / max(attack, 1e-5))


def marimba(note, dur, amp=0.30):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    w = (np.sin(2 * np.pi * f * t)
         + 0.28 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t * 14)
         + 0.16 * np.sin(2 * np.pi * f * 2 * t) * np.exp(-t * 9))
    return w * _env(n, 0.004, 5.2) * amp


def bell(note, dur, amp=0.24):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    w = (np.sin(2 * np.pi * f * t)
         + 0.40 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 5)
         + 0.20 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t * 9))
    return w * _env(n, 0.008, 2.0) * amp


def pluck(note, dur, amp=0.26):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    w = np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 3 * t)
    return w * _env(n, 0.002, 11.0) * amp


def ukulele(note, dur, amp=0.16):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    w = (np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * f * 2 * t)
         + 0.22 * np.sin(2 * np.pi * f * 3 * t))
    return w * _env(n, 0.003, 6.5) * amp


def pad(note, dur, amp=0.10):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    det = [1.0, 1.004, 0.996]
    w = sum(np.sin(2 * np.pi * f * d * t) for d in det) / len(det)
    env = np.minimum(1.0, t / 0.35) * np.minimum(1.0, (dur - t) / 0.5).clip(0)
    return w * env * amp


def bass(note, dur, amp=0.26):
    n = int(SR * dur); t = np.arange(n) / SR; f = midi(note)
    w = np.sin(2 * np.pi * f * t) + 0.30 * np.sin(2 * np.pi * f * 2 * t)
    return w * _env(n, 0.006, 3.0) * amp


def kick(amp=0.34):
    n = int(SR * 0.16); t = np.arange(n) / SR
    f = 118 * np.exp(-t * 34) + 46
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 17) * amp


def _noise(n, seed):
    return np.diff(np.random.default_rng(seed).standard_normal(n), prepend=0.0)


def hat(amp=0.055, dur=0.045):
    n = int(SR * dur); t = np.arange(n) / SR
    return _noise(n, 7) * np.exp(-t * 70) * amp


def shaker(amp=0.035, dur=0.07):
    n = int(SR * dur); t = np.arange(n) / SR
    return _noise(n, 11) * np.exp(-t * 26) * np.minimum(1.0, t / 0.012) * amp


def woodblock(amp=0.09):
    n = int(SR * 0.06); t = np.arange(n) / SR
    return np.sin(2 * np.pi * 1180 * t) * np.exp(-t * 90) * amp


LEADS = {"marimba": marimba, "bell": bell, "pluck": pluck}
COMPS = {"ukulele": ukulele, "pad": pad}


def generate(style="cheerful", duration=30.0, bpm=None, seed=0):
    """지정한 스타일·길이의 스테레오 오디오를 만들어 (samples, 2) 배열로 돌려준다."""
    if style not in PRESETS:
        raise ValueError(f"알 수 없는 스타일: {style} (가능: {', '.join(PRESETS)})")
    p = PRESETS[style]
    bpm = bpm or p["bpm"]
    beat = 60.0 / bpm
    bar = 4 * beat
    total = duration + 0.6
    n_total = int(SR * total)
    L = np.zeros(n_total); R = np.zeros(n_total)
    lead_fn = LEADS[p["lead"]]; comp_fn = COMPS[p["comp"]]
    bars = int(np.ceil(total / bar)) + 1

    def place(buf, sig, t):
        i = int(t * SR)
        if i >= len(buf) or i < 0:
            return
        j = min(len(buf), i + len(sig))
        buf[i:j] += sig[:j - i]

    for b in range(bars):
        t0 = b * bar
        if t0 >= total:
            break
        root, chord = p["prog"][b % len(p["prog"])]

        place(L, bass(root, beat), t0); place(R, bass(root, beat), t0)
        place(L, bass(root + 12, 0.6 * beat, 0.15), t0 + 2.5 * beat)
        place(R, bass(root + 12, 0.6 * beat, 0.15), t0 + 2.5 * beat)

        if p["comp"] == "pad":
            for k, note in enumerate(chord):
                s = comp_fn(note, bar)
                place(L, s * (1.0 - 0.1 * k), t0)
                place(R, s * (0.9 + 0.1 * k), t0)
        else:
            for off in (1.5, 3.5):
                for k, note in enumerate(chord):
                    s = comp_fn(note + 12, 0.55 * beat)
                    place(L, s * (0.75 + 0.25 * (k == 0)), t0 + off * beat + k * 0.006)
                    place(R, s * (0.75 + 0.25 * (k == 2)), t0 + off * beat + k * 0.010)

        if p["drums"] and b >= 1:
            for kb in (0.0, 2.0):
                place(L, kick(), t0 + kb * beat); place(R, kick(), t0 + kb * beat)
            for h in range(8):
                sw = p["swing"] * beat if h % 2 else 0.0
                s = hat(0.055 if h % 2 == 0 else 0.032)
                place(L, s, t0 + h * 0.5 * beat + sw)
                place(R, s, t0 + h * 0.5 * beat + sw)
            for sh in (1.0, 3.0):
                place(L, shaker(), t0 + sh * beat); place(R, shaker(), t0 + sh * beat)
            if p["lead"] == "pluck":
                for wb in (1.5, 3.5):
                    place(L, woodblock(), t0 + wb * beat)
                    place(R, woodblock(), t0 + wb * beat)

        if b >= 1:  # 첫 마디는 반주만 → 자연스러운 도입
            motif = MOTIFS[(b - 1) % len(MOTIFS)]
            for (bt, dur, note) in motif:
                s = lead_fn(note, dur * beat + 0.45) * p["lead_gain"]
                place(L, s * 0.92, t0 + bt * beat)
                place(R, s * 1.00, t0 + bt * beat + 0.011)

    mix = np.stack([L, R], axis=1)[:int(SR * duration)]
    peak = np.abs(mix).max()
    if peak > 0:
        mix *= 0.86 / peak
    mix = np.tanh(mix * 1.12) * 0.90

    fi = int(SR * 0.35); fo = int(SR * min(1.6, duration * 0.2))
    mix[:fi] *= np.linspace(0, 1, fi)[:, None]
    mix[-fo:] *= np.linspace(1, 0, fo)[:, None]
    return mix


def write_wav(mix, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser(description="저작권 프리 BGM 생성기")
    ap.add_argument("--style", default="cheerful", choices=list(PRESETS))
    ap.add_argument("--duration", type=float, default=30.0, help="길이(초)")
    ap.add_argument("--bpm", type=int, default=None, help="템포 (생략 시 프리셋 기본값)")
    ap.add_argument("--out", default="bgm.wav")
    ap.add_argument("--list", action="store_true", help="스타일 목록 출력")
    a = ap.parse_args()
    if a.list:
        for k, v in PRESETS.items():
            print(f"  {k:<9} {v['bpm']:>3} BPM  {v['desc']}")
        return
    write_wav(generate(a.style, a.duration, a.bpm), a.out)
    print(f"BGM 생성 완료: {a.out} ({a.duration:.1f}초, {a.style})")


if __name__ == "__main__":
    main()
