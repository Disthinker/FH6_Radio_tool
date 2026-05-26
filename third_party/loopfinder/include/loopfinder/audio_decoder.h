#pragma once

#include "loopfinder/common.h"

namespace loopfinder {

class AudioDecoder {
public:
    AudioDecoder() = default;
    ~AudioDecoder();

    bool decode(const char* filepath, PCMData& out);

private:
    static AudioFormat detectFormat(const char* filepath);

    bool decodeWAV(const char* path, PCMData& out);
    bool decodeFLAC(const char* path, PCMData& out);
    bool decodeMP3(const char* path, PCMData& out);
    bool decodeOGG(const char* path, PCMData& out);

    void toMono(std::vector<float>& samples, int channels);
    void normalize(std::vector<float>& samples);
    void trimSilence(std::vector<float>& samples, int sampleRate, int& trimStart);
};

} // namespace loopfinder
