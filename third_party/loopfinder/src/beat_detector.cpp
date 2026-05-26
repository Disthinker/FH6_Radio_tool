#include "loopfinder/beat_detector.h"
#include "aubio.h"

#include <algorithm>
#include <cstring>

namespace loopfinder {

BeatDetector::~BeatDetector() {
    if (tempo_) {
        del_aubio_tempo(static_cast<aubio_tempo_t*>(tempo_));
        tempo_ = nullptr;
    }
}

bool BeatDetector::init(int hopSize, int sampleRate) {
    hopSize_ = hopSize;
    uint_t bufSize = 1024;
    tempo_ = new_aubio_tempo("default", bufSize, hopSize, static_cast<uint_t>(sampleRate));
    return tempo_ != nullptr;
}

bool BeatDetector::detect(const float* monoSignal, int signalLen,
                          std::vector<int>& beatFrames, float& bpm) {
    beatFrames.clear();
    bpm = 120.0f;

    if (!tempo_) return false;

    aubio_tempo_t* t = static_cast<aubio_tempo_t*>(tempo_);

    fvec_t* in  = new_fvec(hopSize_);
    fvec_t* out = new_fvec(1);

    int totalHops = signalLen / hopSize_;
    for (int i = 0; i < totalHops; ++i) {
        int offset = i * hopSize_;
        int copyLen = std::min(hopSize_, signalLen - offset);
        for (int j = 0; j < copyLen; ++j)
            in->data[j] = monoSignal[offset + j];
        for (int j = copyLen; j < hopSize_; ++j)
            in->data[j] = 0.0f;

        aubio_tempo_do(t, in, out);
        if (out->data[0] > 0.0f) {
            beatFrames.push_back(i);  // STFT frame index, not sample offset
        }
    }

    bpm = aubio_tempo_get_bpm(t);

    del_fvec(in);
    del_fvec(out);
    return true;
}

} // namespace loopfinder
