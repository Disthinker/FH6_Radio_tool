#include "loopfinder/loopfinder_api.h"
#include "loopfinder/audio_decoder.h"
#include "loopfinder/loop_finder.h"

#include <algorithm>
#include <string>

static std::string g_lastError;

int lf_analyze_file(const char* filepath, int topN,
                    lf_loop_point_t* outPoints, int capacity) {
    g_lastError.clear();

    if (!filepath || !outPoints || capacity <= 0) {
        g_lastError = "invalid arguments";
        return -1;
    }

    loopfinder::PCMData audio;
    loopfinder::AudioDecoder decoder;
    if (!decoder.decode(filepath, audio)) {
        g_lastError = "decode failed";
        return -1;
    }

    loopfinder::LoopFinder::Config config;
    config.topN = topN;

    auto results = loopfinder::LoopFinder().analyze(
        audio.samples.data(),
        static_cast<int>(audio.samples.size()),
        audio.sampleRate,
        config);

    if (results.empty()) {
        g_lastError = "no loop points found";
        return 0;
    }

    int count = std::min(capacity, static_cast<int>(results.size()));
    for (int i = 0; i < count; ++i) {
        outPoints[i].loopStart    = results[i].loopStart + audio.trimStart;
        outPoints[i].loopEnd      = results[i].loopEnd + audio.trimStart;
        outPoints[i].noteDiff     = results[i].noteDiff;
        outPoints[i].loudnessDiff = results[i].loudnessDiff;
        outPoints[i].score        = results[i].score;
    }

    return count;
}

const char* lf_get_last_error() {
    return g_lastError.c_str();
}

