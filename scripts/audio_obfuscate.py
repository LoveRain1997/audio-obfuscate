#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_obfuscate.py  (v2.1)
=========================
在尽量保留音乐节奏骨架的前提下，对音频做“结构性指纹扰动”，
目标是绕过 Suno 等平台 "This audio matches an existing recording" 的录音匹配。

实战经验（默认推荐）
--------------------
单次 medium 可能仍被匹配；把 medium【连续处理两次】（--runs 2，两次完整流程、
使用不同随机曲线）后更容易通过。--runs 即把整套“结构变换 + 音色链”串联执行 N 次。

为什么整体移调 + 轻 EQ 可能失败
------------------------------
现代匹配常用 chroma/旋律特征（对“整体移调”天然不变）或 AI 音频向量（对 EQ/混响/压缩鲁棒）。
本工具的三类“结构性”扰动：
1) 时间轴非线性弹性 (warp)：约 10 秒一段，每段在小幅范围内缓慢变速再交叉淡化拼接，
   归一化使平均速度=1、总时长基本不变、节拍数量与骨架不变，但破坏严格时间对齐。
2) 移调缓慢漂移 (pitch drift)：每段在基准移调上再做缓慢随机漂移，破坏稳定 chroma/旋律特征。
3) 音色/谐波重塑：chorus + aphaser + 极轻 tanh 软饱和 + EQ + 短空间感，改变谐波与音色向量。

重要声明
--------
- 只提高匹配难度，【不保证 100% 绕过】；不改变原作品版权归属。
- 仅可对你【自有或已获授权】的素材使用，并遵守目标平台条款。
- 多轮叠加会累积移调/音色变化（--runs 2 的累计移调约为单轮两倍），听感变化随之增大。

依赖：ffmpeg/ffprobe 在 PATH；Python 需 numpy、scipy。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from scipy.io import wavfile

SR = 44100
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".mp4")
SEG = SR * 10          # 每段 10 秒
XFADE = SR // 25       # 40ms 交叉淡化

# 档位：base 基准移调(半音) / warp 时间弹性幅度 / drift 移调漂移(半音) /
#       sat 软饱和 / chorus / phdec 相位decay / bass / treble / echo / ratio 压缩比
PRESETS = {
    "light": dict(
        base=0.8, warp=0.015, drift=0.10, sat=1.08,
        chorus="0.75:0.65:42|60:0.22|0.18:0.20|0.28:1.5|2",
        phdec=0.18, bass=-0.8, treble=1.4,
        echo="0.86:0.7:18|36:0.10|0.06", ratio=1.8),
    "medium": dict(
        base=1.3, warp=0.025, drift=0.20, sat=1.15,
        chorus="0.85:0.80:45|65:0.30|0.24:0.20|0.30:2|3",
        phdec=0.25, bass=-1.2, treble=2.0,
        echo="0.90:0.80:20|42:0.14|0.09", ratio=2.2),
    "strong": dict(
        base=2.0, warp=0.040, drift=0.35, sat=1.25,
        chorus="0.65:0.55:48|70:0.36|0.30:0.18|0.32:2.5|3.5",
        phdec=0.33, bass=-1.8, treble=2.6,
        echo="0.78:0.55:22|48:0.18|0.12", ratio=2.8),
}


def fail(msg, code=1):
    print("[错误] " + msg, file=sys.stderr)
    sys.exit(code)


def have(t):
    from shutil import which
    return which(t) is not None


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def probe(path):
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "format=duration:stream=sample_rate,channels",
           "-of", "json", path]
    code, out, err = run(cmd)
    if code != 0:
        fail("ffprobe 失败：%s\n%s" % (path, err.strip()))
    d = json.loads(out)
    st = d.get("streams", [{}])[0]
    return {"duration": float(d["format"]["duration"]),
            "sample_rate": int(st.get("sample_rate", SR)),
            "channels": int(st.get("channels", 2))}


def smooth_curve(n, seed):
    """生成缓慢起伏、去均值、峰值约 1 的平滑曲线。"""
    rng = np.random.RandomState(seed)
    r = rng.standard_normal(n)
    if n >= 3:
        k = np.hanning(max(3, n // 2))
        k = k / k.sum()
        r = np.convolve(r, k, mode="same")
    r = r - r.mean()
    m = np.max(np.abs(r))
    return (r / m) if m > 1e-9 else r


def read_audio(path):
    """任意格式 -> float32, shape(n,2)。先用 ffmpeg 统一解码到临时 wav。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    code, _, err = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", path, "-ar", str(SR), "-ac", "2",
                        "-c:a", "pcm_s16le", tmp.name])
    if code != 0:
        os.unlink(tmp.name)
        fail("解码失败：%s\n%s" % (path, err.strip()))
    sr, data = wavfile.read(tmp.name)
    os.unlink(tmp.name)
    x = data.astype(np.float32) / 32768.0
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    return x


def write_wav(path, x):
    y = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
    wavfile.write(path, SR, y)


def rubber_segment(chunk, tempo, pitch_ratio, work, idx):
    """对单段做 rubberband（per段 tempo + pitch），返回 float32(n,2)。"""
    iw = os.path.join(work, "in_%03d.wav" % idx)
    ow = os.path.join(work, "out_%03d.wav" % idx)
    write_wav(iw, chunk)
    rf = ("rubberband=tempo=%.5f:pitch=%.6f:transients=crisp:detector=percussive"
          ":phase=independent:window=long:formant=preserved:pitchq=quality:channels=apart"
          % (tempo, pitch_ratio))
    code, _, err = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", iw, "-af", rf, "-ar", str(SR),
                        "-c:a", "pcm_s16le", ow])
    if code != 0:
        fail("段 %d rubberband 失败：%s" % (idx, err.strip()))
    sr2, d = wavfile.read(ow)
    return d.astype(np.float32) / 32768.0


def append_xfade(out, seg):
    """等功率交叉淡化拼接。"""
    if out is None:
        return seg.astype(np.float32)
    x = min(XFADE, out.shape[0], seg.shape[0])
    fade = np.linspace(0.0, 1.0, x, dtype=np.float32)[:, None]
    a = np.sqrt(1.0 - fade)
    b = np.sqrt(fade)
    blended = out[-x:] * a + seg[:x] * b
    return np.concatenate([out[:-x], blended, seg[x:]], axis=0)


def structural_transform(x, base, warp_amp, drift_amp, global_tempo, seed):
    """warp + pitch drift（分段 rubberband + crossfade）。"""
    n = x.shape[0]
    N = int(np.ceil(n / SEG))

    if warp_amp > 0 and N > 1:
        tempo = 1.0 + smooth_curve(N, seed) * warp_amp
        # 归一化使 mean(1/tempo)=1 -> 总时长≈原始（平均速度=1）
        s = float(np.mean(1.0 / tempo))
        tempo = tempo * s
    else:
        tempo = np.ones(N)
    if global_tempo != 1.0:
        tempo = tempo * global_tempo   # 用户显式整体变速（最后手段）

    if drift_amp > 0 and N > 1:
        drift = smooth_curve(N, seed + 1) * drift_amp
    else:
        drift = np.zeros(N)
    pitch_sem = base + drift
    pitch_ratio = 2.0 ** (pitch_sem / 12.0)

    work = tempfile.mkdtemp(prefix="obf_")
    out = None
    try:
        for i in range(N):
            chunk = x[i * SEG: min(n, (i + 1) * SEG)]
            y = rubber_segment(chunk, float(tempo[i]), float(pitch_ratio[i]), work, i)
            out = append_xfade(out, y)
            print("        段 %02d/%d  tempo %.4f  pitch %+.2f 半音"
                  % (i + 1, N, tempo[i], pitch_sem[i]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def soft_saturate(x, amount):
    if amount <= 1.0:
        return x
    return np.tanh(x * amount) / np.tanh(amount)


def chain_filters(p):
    """音色 + 响度归一滤镜链（中间轮与最终输出共用）。"""
    return [
        "chorus=%s" % p["chorus"],
        "aphaser=in_gain=0.9:out_gain=0.85:delay=3:decay=%.2f:speed=0.6:type=t" % p["phdec"],
        "bass=gain=%.2f:frequency=150" % p["bass"],
        "treble=gain=%.2f:frequency=3200:width_type=h:width=2200" % p["treble"],
        "aecho=%s" % p["echo"],
        "acompressor=threshold=-21dB:ratio=%.1f:attack=12:release=220:knee=2.5:makeup=2" % p["ratio"],
        "loudnorm=I=-14:TP=-1.2:LRA=11",
    ]


def chain_wav_to_wav(wav_in, wav_out, p):
    """中间轮：应用完整音色链，输出 PCM wav。"""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", wav_in, "-filter_complex", ",".join(chain_filters(p)),
           "-ar", str(SR), "-c:a", "pcm_s16le", wav_out]
    code, _, err = run(cmd)
    if code != 0:
        fail("中间轮音色链失败：%s" % err.strip())


def final_chain_to_mp3(wav_in, dst, p, bitrate, keep_meta):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", wav_in, "-filter_complex", ",".join(chain_filters(p)),
           "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", bitrate]
    if not keep_meta:
        cmd += ["-map_metadata", "-1"]
    cmd += [dst]
    code, _, err = run(cmd)
    if code != 0:
        fail("末段编码失败：%s" % err.strip())


def default_tag(runs):
    return "_obf2" if runs <= 1 else "_obf2x%d" % runs


def default_output(src, runs):
    d = os.path.dirname(src)
    name, ext = os.path.splitext(os.path.basename(src))
    return os.path.join(d, name + default_tag(runs) + ".mp3")


def process_one(src, dst, args):
    if not os.path.isfile(src):
        print("[跳过] 不存在：%s" % src)
        return False
    before = probe(src)
    p = PRESETS[args.preset]
    base = args.semitones if args.semitones is not None else p["base"]
    warp_amp = args.warp if args.warp is not None else p["warp"]
    drift_amp = args.drift if args.drift is not None else p["drift"]
    runs = max(1, args.runs)
    seed0 = args.seed if args.seed is not None else int(np.random.randint(1, 99999))

    print("[处理] %s" % os.path.basename(src))
    print("        档位 %s | 轮数 %d | 单轮移调 %+.2f | 弹性 ±%.1f%% | 漂移 ±%.2f | 整体 x%.3f"
          % (args.preset, runs, base, warp_amp * 100, drift_amp, args.tempo))

    x = read_audio(src)
    tmpdir = tempfile.mkdtemp(prefix="obfruns_")
    try:
        for r in range(runs):
            print("    ---- 第 %d/%d 轮 (seed %d) ----" % (r + 1, runs, seed0 + r * 7919))
            x = structural_transform(x, base, warp_amp, drift_amp,
                                     args.tempo, seed0 + r * 7919)
            x = soft_saturate(x, p["sat"])
            cur = os.path.join(tmpdir, "cur_%d.wav" % r)
            write_wav(cur, x)
            if r == runs - 1:
                final_chain_to_mp3(cur, dst, p, args.bitrate, args.keep_meta)
            else:
                nxt = os.path.join(tmpdir, "nxt_%d.wav" % r)
                chain_wav_to_wav(cur, nxt, p)
                sr2, d2 = wavfile.read(nxt)
                x = d2.astype(np.float32) / 32768.0
                if x.ndim == 1:
                    x = np.stack([x, x], axis=1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    after = probe(dst)
    dt = after["duration"] - before["duration"]
    print("[完成] -> %s" % dst)
    print("        时长 %.2fs -> %.2fs (差 %+.2fs) | 单轮平均 BPM 变化 %+.2f%%"
          % (before["duration"], after["duration"], dt, (args.tempo - 1) * 100))
    return True


def collect_inputs(items):
    files = []
    for it in items:
        if os.path.isdir(it):
            for fn in sorted(os.listdir(it)):
                low = fn.lower()
                if low.endswith(AUDIO_EXTS) and "_obf" not in low:
                    files.append(os.path.join(it, fn))
        elif os.path.isfile(it):
            files.append(it)
        else:
            import glob
            files.extend(glob.glob(it))
    seen, uniq = set(), []
    for f in files:
        ap = os.path.abspath(f)
        if ap not in seen:
            seen.add(ap)
            uniq.append(f)
    return uniq


def main():
    ap = argparse.ArgumentParser(
        description="保节奏骨架的音频指纹扰动 v2.1（rubberband + numpy，支持 --runs 多轮）。")
    ap.add_argument("inputs", nargs="+", help="音频/视频文件 / 文件夹 / 通配（可多个）")
    ap.add_argument("-o", "--out", default=None, help="输出文件或目录；默认加 _obf2 / _obf2xN 后缀")
    ap.add_argument("--preset", choices=list(PRESETS), default="medium")
    ap.add_argument("--runs", type=int, default=1,
                    help="完整处理的轮数，默认1；实战推荐 --runs 2（medium 连跑两次更易过）")
    ap.add_argument("--semitones", type=float, default=None, help="单轮基准移调半音（覆盖档位）")
    ap.add_argument("--warp", type=float, default=None, help="时间弹性幅度，如 0.025；0 关闭")
    ap.add_argument("--drift", type=float, default=None, help="移调漂移(半音)，0 关闭")
    ap.add_argument("--tempo", type=float, default=1.0, help="整体速度倍率，默认1（最后手段）")
    ap.add_argument("--bitrate", default="320k")
    ap.add_argument("--seed", type=int, default=None, help="首轮随机种子（可复现）")
    ap.add_argument("--keep-meta", action="store_true", help="保留元数据（默认清除）")
    args = ap.parse_args()

    if not have("ffmpeg") or not have("ffprobe"):
        fail("未找到 ffmpeg/ffprobe。")

    files = collect_inputs(args.inputs)
    if not files:
        fail("没有可处理的音视频。")

    multi = len(files) > 1
    out_dir = args.out if (args.out and os.path.isdir(args.out)) else None
    tag = default_tag(max(1, args.runs))
    ok = 0
    for src in files:
        if multi or out_dir:
            d = out_dir or os.path.dirname(src)
            name, _ext = os.path.splitext(os.path.basename(src))
            dst = os.path.join(d, name + tag + ".mp3")
        else:
            dst = args.out or default_output(src, max(1, args.runs))
        if process_one(src, dst, args):
            ok += 1
        print("-" * 70)
    print("全部结束：成功 %d/%d。" % (ok, len(files)))
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
