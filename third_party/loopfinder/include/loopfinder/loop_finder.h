#pragma once

#include <vector>
#include "loopfinder/common.h"

namespace loopfinder {

class LoopFinder {
public:
    struct Config {
        float minDurationMultiplier = 0.35f;
        float minLoopDurationSec    = 0.0f;
        float maxLoopDurationSec    = 0.0f;
        int   topN                  = 5;
        int   nFFT                  = 2048;
        int   hopSize               = 512;
    };

    LoopFinder() = default;

    std::vector<LoopPoint> analyze(const float* monoSignal, int signalLen,
                                   int sampleRate, const Config& config);

private:
    void findCandidatePairs(const std::vector<std::vector<float>>& chroma,
                            const std::vector<std::vector<float>>& powerDB,
                            const std::vector<int>& beats,
                            int minLoopFrames, int maxLoopFrames,
                            std::vector<LoopPoint>& candidates);

    void scoreCandidates(const std::vector<std::vector<float>>& chroma,
                         float bpm, int hopSize, int sampleRate,
                         std::vector<LoopPoint>& candidates);

    void prioritizeDuration(std::vector<LoopPoint>& candidates);

    static float cosineSimilarity(const float* a, const float* b, int len);
    static float dotProduct(const float* a, const float* b, int len);
    static float vectorNorm(const float* v, int len);
    static void  geometricWeights(int len, float* weights, float start, float stop);
    static float waveformContinuity(const float* signal, int signalLen,
                                     int startSample, int endSample,
                                     int windowLen);
    static void  normalizeChroma(std::vector<std::vector<float>>& chroma);
};

} // namespace loopfinder
