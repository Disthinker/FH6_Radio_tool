#pragma once

#include <vector>

namespace loopfinder {

class BeatDetector {
public:
    BeatDetector() = default;
    ~BeatDetector();

    bool init(int hopSize, int sampleRate);
    bool detect(const float* monoSignal, int signalLen,
                std::vector<int>& beatFrames, float& bpm);

    int hopSize_ = 512;

private:
    void* tempo_ = nullptr;  // aubio_tempo_t (opaque)
};

} // namespace loopfinder
