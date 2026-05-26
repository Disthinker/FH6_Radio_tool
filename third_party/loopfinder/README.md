# loopfinder — C++ 音频循环点检测库

PyMusicLooper 的 C++ 重写版本，编译为 `libloopfinder.so`，通过 JNI 供 Android 调用。

## 目录结构

```
loopfinder/
├── CMakeLists.txt                  # 顶层构建配置
├── include/loopfinder/             # 公共头文件
│   ├── common.h                    # 数据结构 (PCMData, LoopPoint, STFTResult)
│   ├── audio_decoder.h             # 音频解码接口 (dr_libs)
│   ├── stft.h                      # 短时傅里叶变换 (KissFFT)
│   ├── hpss.h                      # 谐波/打击乐分离
│   ├── chroma.h                    # Chroma 色度特征提取
│   ├── beat_detector.h             # 节拍检测 (aubio)
│   ├── loop_finder.h              # 核心循环查找算法
│   └── loopfinder_api.h           # C API (FFI / CGo / Python ctypes 友好)
├── src/
│   ...
│   └── loopfinder_api.cpp         # C API 实现
├── jni/
│   └── jni_main.cpp                # JNI 入口点
└── third_party/                    # 第三方库 (需手动放入)
    ├── dr_wav.h, dr_flac.h, dr_mp3.h
    ├── stb_vorbis.c, stb_vorbis_impl.c
    ├── kiss_fft.h/c, kiss_fftr.h/c, _kiss_fft_guts.h
    └── aubio/                      # 完整 aubio 源码
        └── src/
            ├── config.h            # ← 项目生成的最小配置
            └── ...                 # 39 个 .c/.h 手动编译
```

## 第三方库依赖

| 库 | 所需文件 | 来源 |
|---|---|---|
| **dr_libs** | `dr_wav.h`, `dr_flac.h`, `dr_mp3.h` | https://github.com/mackron/dr_libs |
| **stb** | `stb_vorbis.c` | https://github.com/nothings/stb |
| **KissFFT** | `kiss_fft.h/c`, `kiss_fftr.h/c`, `_kiss_fft_guts.h` | https://github.com/mborgerding/kissfft |
| **aubio** | 完整源码放入 `third_party/aubio/` | https://github.com/aubio/aubio |

aubio 自带 Ooura FFT 后端，零外部依赖。已生成 `config.h` 做最小配置。
CMakeLists.txt 逐文件列出 19 个 `.c` 源文件编译，不使用 waf。

> 推荐 git submodule:
> ```bash
> git submodule add https://github.com/aubio/aubio.git third_party/aubio
> ```

## 构建

### 桌面端 (测试用)

```bash
cmake -B build -DBUILD_FOR_ANDROID=OFF -DBUILD_TEST=ON
cmake --build build
```

### Android NDK (交叉编译)

```bash
cmake -B build \
    -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
    -DANDROID_ABI=arm64-v8a \
    -DANDROID_PLATFORM=android-24 \
    -DBUILD_FOR_ANDROID=ON
cmake --build build
```

输出：`build/libloopfinder.so`

## JNI 接口

```java
package com.cpu.seamlessloopmobile.jni;

data class LoopPoint(
    val loopStart: Long,    // 采样点 (相对原始文件)
    val loopEnd: Long,      // 采样点 (相对原始文件)
    val noteDiff: Float,    // 音符距离
    val loudnessDiff: Float,// 响度差异
    val score: Float        // 余弦相似度评分
)

class NativeAudio {
    external fun analyzeLoopPoints(filePath: String, topN: Int): Array<LoopPoint>
}
```

JNI 函数签名:
```
Java_com_cpu_seamlessloopmobile_jni_NativeAudio_analyzeLoopPoints(
    JNIEnv* env, jclass, jstring filePath, jint topN
) -> jobjectArray (LoopPoint[])
```

## 算法流水线

```
音频文件
    ↓ dr_libs 解码
PCM float32 单声道
    ↓ Trim 静音 + 归一化
    ↓ STFT (KissFFT, Hann 窗, n_fft=2048, hop=512)
功率谱 (|S|²)
    ↓ HPSS 谐波分离 (2D 中值滤波, 软掩膜)
谐波谱 → Chroma 提取 (12 半音, C1-C8)
功率谱 → A-Weighting → dB 转换
原始信号 → aubio 节拍检测 (Ooura FFT, 无外部依赖)
    ↓
候选对枚举 (按节拍, 音符距离 ≤ 0.0875, 响度差 ≤ 0.5)
    ↓
余弦相似度打分 (向前/向后 12 个节拍, 几何衰减权重)
    ↓
时长优先 (相似度接近时优先长循环)
    ↓
帧索引 → 采样点 → +trim偏移
    ↓
Top N 返回
```

## 与 Python 版本的差异

### 已实现
- 音频解码 (WAV/FLAC/MP3/OGG)
- STFT + Chroma + 响度提取
- HPSS 谐波/打击乐分离 (Python 版本未使用，此为增强)
- aubio 节拍检测 (逐文件编译，零外部依赖)
- 候选对枚举 + 余弦相似度打分 + 时长优先
- 帧→采样点转换 + trim 偏移补偿
- 高度对齐python打分
### 未实现 (不需要)
- 实时播放 (Oboe 已处理)
- 音频导出 (split-audio/extend)
- 元数据标签写入 (Room DB 已处理)
- 网络流 (yt-dlp)
- 交互模式 (Android UI 处理)
- M4A/AAC 解码 (dr_libs 不支持)
- FFmpeg (Android 上过重)
- 过零检测微调 (可选优化，当前使用直接帧→采样映射)
