#include "loopfinder/audio_decoder.h"

#define DR_WAV_IMPLEMENTATION
#include "dr_wav.h"
#define DR_FLAC_IMPLEMENTATION
#include "dr_flac.h"
#define DR_MP3_IMPLEMENTATION
#include "dr_mp3.h"
#include "stb_vorbis.c"

#include <algorithm>
#include <cmath>
#include <cstring>

#ifdef _WIN32
#define strcasecmp _stricmp
#endif

namespace loopfinder {

AudioDecoder::~AudioDecoder() {}

AudioFormat AudioDecoder::detectFormat(const char* filepath) {
    const char* ext = std::strrchr(filepath, '.');
    if (!ext) return AudioFormat::UNKNOWN;
    if (strcasecmp(ext, ".wav") == 0 || strcasecmp(ext, ".wave") == 0) return AudioFormat::WAV;
    if (strcasecmp(ext, ".flac") == 0) return AudioFormat::FLAC;
    if (strcasecmp(ext, ".mp3") == 0)  return AudioFormat::MP3;
    if (strcasecmp(ext, ".ogg") == 0)  return AudioFormat::OGG;
    return AudioFormat::UNKNOWN;
}

bool AudioDecoder::decode(const char* filepath, PCMData& out) {
    AudioFormat fmt = detectFormat(filepath);
    switch (fmt) {
        case AudioFormat::WAV:  return decodeWAV(filepath, out);
        case AudioFormat::FLAC: return decodeFLAC(filepath, out);
        case AudioFormat::MP3:  return decodeMP3(filepath, out);
        case AudioFormat::OGG:  return decodeOGG(filepath, out);
        default: return false;
    }
}

bool AudioDecoder::decodeWAV(const char* path, PCMData& out) {
    drwav wav;
    if (!drwav_init_file(&wav, path, nullptr)) return false;

    out.sampleRate  = static_cast<int>(wav.sampleRate);
    out.numChannels = wav.channels;
    out.totalSamples = static_cast<int64_t>(wav.totalPCMFrameCount);

    std::vector<float> interleaved(out.totalSamples * out.numChannels);
    drwav_read_pcm_frames_f32(&wav, wav.totalPCMFrameCount, interleaved.data());
    drwav_uninit(&wav);

    out.samples.resize(out.totalSamples);
    if (out.numChannels == 1) {
        std::copy(interleaved.begin(), interleaved.end(), out.samples.begin());
    } else {
        toMono(interleaved, out.numChannels);
        out.samples.resize(out.totalSamples);
        for (int64_t i = 0; i < out.totalSamples; ++i)
            out.samples[i] = interleaved[i];
    }
    normalize(out.samples);
    trimSilence(out.samples, out.sampleRate, out.trimStart);
    return true;
}

bool AudioDecoder::decodeFLAC(const char* path, PCMData& out) {
    drflac* flac = drflac_open_file(path, nullptr);
    if (!flac) return false;

    out.sampleRate  = flac->sampleRate;
    out.numChannels = flac->channels;
    out.totalSamples = static_cast<int64_t>(flac->totalPCMFrameCount);

    std::vector<float> interleaved(out.totalSamples * out.numChannels);
    drflac_read_pcm_frames_f32(flac, flac->totalPCMFrameCount, interleaved.data());
    drflac_close(flac);

    out.samples.resize(out.totalSamples);
    if (out.numChannels == 1) {
        std::copy(interleaved.begin(), interleaved.end(), out.samples.begin());
    } else {
        toMono(interleaved, out.numChannels);
        out.samples.resize(out.totalSamples);
        for (int64_t i = 0; i < out.totalSamples; ++i)
            out.samples[i] = interleaved[i];
    }
    normalize(out.samples);
    trimSilence(out.samples, out.sampleRate, out.trimStart);
    return true;
}

bool AudioDecoder::decodeMP3(const char* path, PCMData& out) {
    drmp3 mp3;
    if (!drmp3_init_file(&mp3, path, nullptr)) return false;

    out.sampleRate  = static_cast<int>(mp3.sampleRate);
    out.numChannels = mp3.channels;
    out.totalSamples = static_cast<int64_t>(drmp3_get_pcm_frame_count(&mp3));

    std::vector<float> interleaved(out.totalSamples * out.numChannels);
    drmp3_read_pcm_frames_f32(&mp3, out.totalSamples, interleaved.data());
    drmp3_uninit(&mp3);

    out.samples.resize(out.totalSamples);
    if (out.numChannels == 1) {
        std::copy(interleaved.begin(), interleaved.end(), out.samples.begin());
    } else {
        toMono(interleaved, out.numChannels);
        out.samples.resize(out.totalSamples);
        for (int64_t i = 0; i < out.totalSamples; ++i)
            out.samples[i] = interleaved[i];
    }
    normalize(out.samples);
    trimSilence(out.samples, out.sampleRate, out.trimStart);
    return true;
}

bool AudioDecoder::decodeOGG(const char* path, PCMData& out) {
    int channels = 0, sampleRate = 0;
    short* raw = nullptr;
    int numSamples = stb_vorbis_decode_filename(path, &channels, &sampleRate, &raw);
    if (numSamples < 0) return false;

    out.sampleRate  = sampleRate;
    out.numChannels = channels;
    out.totalSamples = numSamples;

    out.samples.resize(numSamples);
    if (channels == 1) {
        for (int i = 0; i < numSamples; ++i)
            out.samples[i] = raw[i] / 32768.0f;
    } else {
        for (int i = 0; i < numSamples; ++i) {
            float sum = 0.0f;
            for (int ch = 0; ch < channels; ++ch)
                sum += raw[i * channels + ch] / 32768.0f;
            out.samples[i] = sum / channels;
        }
    }
    free(raw);
    normalize(out.samples);
    trimSilence(out.samples, out.sampleRate, out.trimStart);
    return true;
}

void AudioDecoder::toMono(std::vector<float>& samples, int channels) {
    if (channels <= 1) return;
    int64_t numFrames = samples.size() / channels;
    for (int64_t i = 0; i < numFrames; ++i) {
        float sum = 0.0f;
        for (int ch = 0; ch < channels; ++ch)
            sum += samples[i * channels + ch];
        samples[i] = sum / channels;
    }
    samples.resize(numFrames);
}

void AudioDecoder::normalize(std::vector<float>& samples) {
    float maxAbs = 0.0f;
    for (float s : samples)
        maxAbs = std::max(maxAbs, std::abs(s));
    if (maxAbs > 1e-8f) {
        float invMax = 1.0f / maxAbs;
        for (float& s : samples) s *= invMax;
    }
}

void AudioDecoder::trimSilence(std::vector<float>& samples, int sampleRate, int& trimStart) {
    trimStart = 0;
    // Use 40 dB threshold like librosa.effects.trim
    float dB = 40.0f;
    float ref = 1.0f;
    float threshold = ref * std::pow(10.0f, -dB / 20.0f);

    // Frame-based RMS for smoother trimming
    int frameLen = sampleRate / 20; // 50ms frames
    if (frameLen < 256) frameLen = 256;

    int64_t numFrames = samples.size() / frameLen;
    if (numFrames < 2) return;

    std::vector<float> rmsPerFrame(numFrames);
    for (int64_t f = 0; f < numFrames; ++f) {
        float sumSq = 0.0f;
        int64_t start = f * frameLen;
        int64_t end = std::min(static_cast<int64_t>(start + frameLen),
                                static_cast<int64_t>(samples.size()));
        for (int64_t i = start; i < end; ++i)
            sumSq += samples[i] * samples[i];
        rmsPerFrame[f] = std::sqrt(sumSq / (end - start));
    }

    // Find first frame above threshold
    int startFrame = 0;
    while (startFrame < numFrames && rmsPerFrame[startFrame] <= threshold)
        ++startFrame;

    // Find last frame above threshold
    int endFrame = static_cast<int>(numFrames) - 1;
    while (endFrame >= 0 && rmsPerFrame[endFrame] <= threshold)
        --endFrame;

    if (startFrame >= endFrame) return;

    int64_t cutStart = static_cast<int64_t>(startFrame) * frameLen;

    int64_t samplesSize = static_cast<int64_t>(samples.size());
    int64_t endFramePos = static_cast<int64_t>(endFrame + 1) * frameLen;
    int64_t cutEnd = std::min(endFramePos, samplesSize);

    trimStart = static_cast<int>(cutStart);
    std::vector<float> trimmed(samples.begin() + cutStart, samples.begin() + cutEnd);
    samples = std::move(trimmed);
}

} // namespace loopfinder
