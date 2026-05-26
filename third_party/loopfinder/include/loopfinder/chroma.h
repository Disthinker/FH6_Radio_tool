#pragma once

#include <vector>

namespace loopfinder {

class ChromaExtractor {
public:
    static constexpr int NUM_CHROMA = 12;

    void extract(const std::vector<std::vector<float>>& powerSpec,
                 int sampleRate, int nFFT,
                 std::vector<std::vector<float>>& chromagram);

    int numFrames() const { return numFrames_; }

private:
    int numFreqBins_ = 0;
    int numFrames_   = 0;
};

} // namespace loopfinder
