#include "loopfinder/stft.h"

#include "kiss_fftr.h"

#include <cmath>
#include <cstring>

namespace loopfinder {

STFT::~STFT() {
    if (fftCfg_)  kiss_fftr_free(static_cast<kiss_fftr_cfg>(fftCfg_));
    if (hannWindow_) delete[] hannWindow_;
    // fftIn_ and fftOut_ are stack-allocated per frame; no persistent buffers needed
    fftCfg_  = nullptr;
    hannWindow_ = nullptr;
}

bool STFT::init(int nfft, int hop) {
    nFFT    = nfft;
    hopSize = hop;

    fftCfg_ = kiss_fftr_alloc(nFFT, 0, nullptr, nullptr);
    if (!fftCfg_) return false;

    hannWindow_ = new float[nFFT];
    for (int i = 0; i < nFFT; ++i) {
        hannWindow_[i] = 0.5f * (1.0f - std::cos(2.0f * 3.14159265358979f * i / (nFFT - 1)));
    }
    return true;
}

int STFT::getNumFrames(int signalLen) const {
    if (signalLen < nFFT) return 0;
    return (signalLen - nFFT) / hopSize + 1;
}

void STFT::compute(const float* signal, int signalLen,
                   std::vector<std::vector<float>>& magnitude,
                   int& numFreqBins, int& numFrames) {
    int nFrames = getNumFrames(signalLen);
    int nBins   = nFFT / 2 + 1;

    magnitude.assign(nBins, std::vector<float>(nFrames, 0.0f));

    kiss_fft_scalar* in  = new kiss_fft_scalar[nFFT];
    kiss_fft_cpx*    out = new kiss_fft_cpx[nBins];

    for (int frame = 0; frame < nFrames; ++frame) {
        int start = frame * hopSize;
        std::memset(in, 0, nFFT * sizeof(kiss_fft_scalar));

        int copyLen = std::min(nFFT, signalLen - start);
        for (int i = 0; i < copyLen; ++i)
            in[i] = signal[start + i] * hannWindow_[i];

        kiss_fftr(static_cast<kiss_fftr_cfg>(fftCfg_), in, out);

        for (int k = 0; k < nBins; ++k) {
            magnitude[k][frame] = std::sqrt(out[k].r * out[k].r + out[k].i * out[k].i);
        }
    }

    delete[] in;
    delete[] out;

    numFreqBins = nBins;
    numFrames   = nFrames;
}

void STFT::computePower(const float* signal, int signalLen,
                        std::vector<std::vector<float>>& powerSpec,
                        int& numFreqBins, int& numFrames) {
    std::vector<std::vector<float>> mag;
    compute(signal, signalLen, mag, numFreqBins, numFrames);

    powerSpec.assign(numFreqBins, std::vector<float>(numFrames, 0.0f));
    for (int f = 0; f < numFreqBins; ++f)
        for (int t = 0; t < numFrames; ++t)
            powerSpec[f][t] = mag[f][t] * mag[f][t];
}

} // namespace loopfinder
