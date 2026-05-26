#pragma once

#include <vector>

namespace loopfinder {

class STFT {
public:
    STFT() = default;
    ~STFT();

    bool init(int nFFT, int hopSize);
    void compute(const float* signal, int signalLen,
                 std::vector<std::vector<float>>& magnitude,
                 int& numFreqBins, int& numFrames);
    void computePower(const float* signal, int signalLen,
                      std::vector<std::vector<float>>& powerSpec,
                      int& numFreqBins, int& numFrames);

    int getNumFrames(int signalLen) const;

    int hopSize = 512;
    int nFFT    = 2048;

private:
    float* hannWindow_ = nullptr;
    void*  fftCfg_     = nullptr;   // kiss_fftr_cfg (opaque)
};

} // namespace loopfinder
