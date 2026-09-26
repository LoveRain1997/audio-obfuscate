# audio-obfuscate

**保节奏的音频指纹扰动工具（Tempo-preserving audio fingerprint perturbation）**

在不改变音乐**平均速度、节拍（BPM）骨架与时长**的前提下，对音频做变换，降低被音频指纹 / “录音匹配（recording match）”命中的概率。支持 `--runs` 把整套处理连续执行多轮。同时也是一个可被 AI Agent 加载的 **Skill**（见 `SKILL.md`）。

## 它做什么
- **时间轴非线性弹性（warp）**：把音频按约 10 秒分段，每段在小幅范围内缓慢改变速度，再交叉淡化拼接；归一化使平均速度恒为 1、总时长基本不变，但破坏指纹所需的严格时间对齐。
- **移调 + 缓慢漂移（pitch / drift）**：整体移调并随时间缓慢漂移，平移频谱峰值、破坏对移调不变的 chroma / 旋律特征。
- **音色 / 谐波重塑**：`chorus` 合唱、`aphaser` 相位、极轻 `tanh` 软饱和、EQ 与短空间感，改变谐波结构与音色向量。
- **多轮串联（--runs）**：把“结构变换 + 音色链”连续执行 N 轮，轮间以 WAV 传递、自动使用不同随机曲线；移调/音色随轮数叠加。
- 每轮末尾 `loudnorm` 把响度归一到 −14 LUFS（流媒体标准），默认清除元数据。

## 依赖
- [`ffmpeg`](https://ffmpeg.org/) / `ffprobe` 已安装并在 `PATH`（需要 `rubberband` 滤镜）。
- Python 3.9+，以及 `numpy`、`scipy`。

检查：
```bash
ffmpeg -hide_banner -filters | findstr rubberband
```

## 快速使用
```bash
# 实战首选：medium 连跑两次（单次不过时），输出 “输入_obf2x2.mp3”
python scripts/audio_obfuscate.py "输入" --runs 2 --seed 2026

# 单次 medium，输出 “输入_obf2.mp3”
python scripts/audio_obfuscate.py "输入"

# 指定输出
python scripts/audio_obfuscate.py "输入" -o "输出.mp3"

# 批量处理整个文件夹（自动跳过 *_obf*）
python scripts/audio_obfuscate.py "某文件夹" --runs 2
```
输入支持 mp3 / wav / flac / m4a，也支持含音轨的 mp4。

## 策略与档位（从弱到强，选“能过的最低强度”最自然）
| 方式 | 单轮移调 | 时间弹性 | 轮数 |
|---|---|---|---|
| `--preset light`  | +0.8 半音 | ±1.5% | 1 |
| `--preset medium` | +1.3 半音 | ±2.5%（默认） | 1 |
| `--preset medium --runs 2` | +1.3（两轮叠加） | ±2.5% | **2（实战推荐）** |
| `--preset strong` | +2.0 半音 | ±4.0% | 1 |
| `--preset strong --runs 2` | +2.0（两轮叠加） | ±4.0% | 2 |

进一步加码（代价随之上升）：
```bash
python scripts/audio_obfuscate.py "输入" --warp 0.06 --drift 0.5 --runs 2  # 更强扭曲，节奏轻微“晃”
python scripts/audio_obfuscate.py "输入" --tempo 1.04                      # 最后手段：整体变速，会改 BPM
```

## 全部参数
```
--preset {light,medium,strong}   单轮强度档位
--runs N                         完整处理轮数（默认 1，实战推荐 2）
--semitones N                    单轮基准移调半音（覆盖档位，可为负数）
--warp A                         时间弹性幅度（0.025 = ±2.5%，0 关闭）
--drift D                        移调漂移幅度（半音）
--tempo T                        整体速度倍率（默认 1.0）
--bitrate 320k                   输出 MP3 码率
--seed N                         首轮随机种子（可复现）
--keep-meta                      保留元数据（默认清除）
```

## 原理速览
- 传统指纹（Shazam 类）依赖“时间-频率峰值对” → 移调 + warp 使其大量失配。
- chroma / 旋律类对整体移调不变 → 需要 drift 漂移 + warp 时间形变，多轮叠加进一步拉开。
- AI 听感 / 向量对 EQ、混响鲁棒 → 需要 chorus / phaser / 软饱和改变谐波，必要时更多轮。

## 免责声明
本工具仅提高音频指纹的匹配难度，**不保证一定绕过任何平台的识别**，也**不改变原作品的版权归属**。请仅对你**拥有版权或已获授权**的素材使用，并自行遵守目标平台的服务条款与适用法律。因不当使用产生的一切后果由使用者承担。

## License
[MIT](LICENSE)
