#pragma once

#include <cstdint>
#include <vector>

namespace loopfinder {

struct PCMData {
    std::vector<float> samples;   // mono, normalized [-1, 1]
    int   sampleRate = 0;
    int   numChannels = 0;
    int64_t totalSamples = 0;     // original total before trim
    int   trimStart = 0;          // offset in original samples where trimmed audio starts
};

struct LoopPoint {
    int64_t loopStart     = 0;    // in samples (relative to original, not trimmed)
    int64_t loopEnd       = 0;    // in samples (relative to original, not trimmed)
    float   noteDiff      = 0.0f;
    float   loudnessDiff  = 0.0f;
    float   score         = 0.0f;
};

struct STFTResult {
    std::vector<std::vector<float>> magnitude;   // [freqBin][frame]
    int numFreqBins = 0;
    int numFrames   = 0;
    int hopSize     = 512;
    int nFFT        = 2048;
};

enum class AudioFormat {
    WAV,
    FLAC,
    MP3,
    OGG,
    UNKNOWN
};

} // namespace loopfinder
