#include <iostream>
#include <vector>

#include "loopfinder/loop_finder.h"
#include "loopfinder/audio_decoder.h"

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "usage: loopfinder_test audio.wav\n";
        return 1;
    }

    using namespace loopfinder;

    PCMData audio;
    AudioDecoder decoder;
    if (!decoder.decode(argv[1], audio)) {
        std::cerr << "decode failed: " << argv[1] << "\n";
        return 1;
    }

    std::cout << "loaded: " << argv[1]
              << "  sr=" << audio.sampleRate
              << "  samples=" << audio.samples.size()
              << "  trimStart=" << audio.trimStart << "\n";

    LoopFinder finder;
    LoopFinder::Config config;
    config.topN = 5;

    auto results = finder.analyze(
        audio.samples.data(),
        static_cast<int>(audio.samples.size()),
        audio.sampleRate,
        config);

    std::cout << "found " << results.size() << " loop points:\n";
    for (auto& r : results) {
        std::cout << "  start=" << r.loopStart
                  << "  end=" << r.loopEnd
                  << "  noteDiff=" << r.noteDiff
                  << "  loudnessDiff=" << r.loudnessDiff
                  << "  score=" << r.score << "\n";
    }

    return 0;
}
