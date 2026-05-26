#include "loopfinder/chroma.h"

#include <algorithm>
#include <cmath>

namespace loopfinder {

void ChromaExtractor::extract(const std::vector<std::vector<float>>& powerSpec,
                              int sampleRate, int nFFT,
                              std::vector<std::vector<float>>& chromagram) {
    numFreqBins_ = static_cast<int>(powerSpec.size());
    numFrames_   = static_cast<int>(powerSpec[0].size());

    chromagram.assign(NUM_CHROMA, std::vector<float>(numFrames_, 0.0f));

    // Map FFT bins to chroma bins (C1 ~ 32.7 Hz to C8 ~ 4186 Hz)
    const float C1 = 32.7032f;
    const float C8 = 4186.01f;

    for (int k = 0; k < numFreqBins_; ++k) {
        float freq = (k * sampleRate) / static_cast<float>(nFFT);
        if (freq < C1 || freq > C8) continue;

        float midi = 12.0f * std::log2(freq / 440.0f) + 69.0f;
        int chromaBin = static_cast<int>(std::round(midi)) % 12;
        if (chromaBin < 0) chromaBin += 12;

        for (int t = 0; t < numFrames_; ++t) {
            chromagram[chromaBin][t] += powerSpec[k][t];
        }
    }

    // Normalize each frame
    for (int t = 0; t < numFrames_; ++t) {
        float sum = 0.0f;
        for (int c = 0; c < NUM_CHROMA; ++c)
            sum += chromagram[c][t];
        if (sum > 1e-10f) {
            float invSum = 1.0f / sum;
            for (int c = 0; c < NUM_CHROMA; ++c)
                chromagram[c][t] *= invSum;
        }
    }
}

} // namespace loopfinder
