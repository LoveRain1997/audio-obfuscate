---
name: audio-obfuscate
description: 音频指纹扰动 / 反“录音匹配”工具。在不改变音乐平均速度、节拍(BPM)骨架与时长的前提下，基于 ffmpeg rubberband 对音频做时间轴非线性弹性伸缩(warp)、移调及缓慢漂移(pitch/drift)、合唱/相位/软饱和等音色重塑，降低被 Suno 等平台 “This audio matches an existing recording / matched to a known recording” 命中的概率。当用户需要：让上传的歌曲/伴奏识别不出原版、规避音频指纹或版权录音匹配、对参考音频做“保节奏的伪装/变换”、批量给音频去指纹时使用。提供 light/medium/strong 三档，支持文件夹批量与随机抖动。
---

# Audio Obfuscate（保节奏的音频指纹扰动）

## 概述
把用户提供的音频变换成“平均速度/BPM/时长不变、节拍骨架保留”，但频谱峰值时间位置、音高关系与谐波音色均被改变的版本，用于提高音频指纹/录音匹配的失配率。核心脚本：`scripts/audio_obfuscate.py`。

## 依赖（先检查，缺失则提示安装）
- `ffmpeg`、`ffprobe` 在 PATH（必需，用 `rubberband` 滤镜）。
- Python 的 `numpy`、`scipy`。
- 检查：`ffmpeg -hide_banner -filters | findstr rubberband`。

## 快速开始
```bash
python scripts/audio_obfuscate.py "输入.mp3"                      # 默认 medium -> 输入_obf2.mp3
python scripts/audio_obfuscate.py "输入.mp3" -o "输出.mp3"        # 指定输出
python scripts/audio_obfuscate.py "某文件夹" --seed 2026          # 批量（自动跳过 *_obf*）
```

## 档位与升级路径（从弱到强，选“能过的最低档”最自然）
1. `--preset light`  ：移调 +0.8、弹性 ±1.5%
2. `--preset medium` ：移调 +1.3、弹性 ±2.5%（默认）
3. `--preset strong` ：移调 +2.0、弹性 ±4%
4. 仍被匹配再逐项加码：
   ```bash
   --warp 0.06 --drift 0.5      # 更强时间/音高扭曲（节奏会轻微“晃”）
   --tempo 1.04                 # 最后手段：整体变速，会真正改变 BPM
   ```
   每次换 `--seed` 可得到不同扰动；`--semitones` 可改移调量（含负数）。

## 关键参数
- `--preset light|medium|strong`：整体强度。
- `--semitones N`：基准移调半音，覆盖档位（如 1.3 / -0.6 / 2.0）。
- `--warp A`：时间弹性幅度（0.025=±2.5%，0 关闭→刚性节奏）。
- `--drift D`：移调缓慢漂移幅度（半音，破坏 chroma 稳定）。
- `--tempo T`：整体速度倍率，默认 1.0。
- `--bitrate 320k`、`--seed N`、`--keep-meta`（默认清除元数据）。

## 工作原理（用于判断该用多强）
- 传统指纹（Shazam 类）依赖“时间-频率峰值对”：移调 + warp 即可大量失配。
- chroma/旋律类对“整体移调”不变：需 `--drift` 漂移 + `--warp` 时间形变。
- AI 听感/向量对 EQ/混响鲁棒：需 chorus/aphaser/软饱和改变谐波，必要时更大 warp。
- warp 经归一化使 `mean(1/tempo)=1`，故平均速度与总时长基本不变；输出末尾 `loudnorm` 归一到 −14 LUFS。

## 验证（交付前必做）
- 脚本会打印前后时长与“平均 BPM 变化”：tempo=1 时应为 `+0.00%`，时长仅差零点几秒（交叉淡化重叠所致）。
- 电平：`ffmpeg -i 输出 -af volumedetect -f null -`，`max_volume` 应 < 0dB（无削波），并与原曲响度接近。
- 可选：`showspectrumpic` 对比前后频谱，确认时间轴偏移、纹理改变而段落骨架保留。
- 让用户**核对上传文件名后缀**（`_obf2`/`_obf3`），避免误传旧文件或原曲，并回报“具体哪个文件 + 结果”。

## 合规边界
- 仅提高匹配难度，**不保证 100% 绕过**；平台升级识别（旋律/AI 听感）后仍可能命中。
- 处理**不改变版权归属**；仅可对用户自有或已获授权的素材使用，并遵守目标平台服务条款。
- 若 strong 仍被识别，不建议继续硬扭（会明显跑调/晃节奏），应建议更换素材、改用其他功能，或重新演奏/翻唱录制。
