#include "loopfinder/loop_finder.h"
#include "loopfinder/beat_detector.h"
#include "loopfinder/chroma.h"
#include "loopfinder/hpss.h"
#include "loopfinder/stft.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <numeric>

#ifdef LOOPFINDER_DEBUG
#define LF_LOG(fmt, ...) fprintf(stderr, fmt "\n", ##__VA_ARGS__)
#else
#define LF_LOG(fmt, ...) ((void)0)
#endif

#define LF_PERF(step, ms) \
    fprintf(stderr, "[loopfinder] %s: %lld ms\n", step, (long long)(ms))

namespace loopfinder {

static int nearestZeroCrossing(const float* audio, int totalSamples, int rate, int sampleIdx) {
    int windowSize = std::max(1, rate / 100);
    int offset = windowSize / 2;

    int negOffset = std::max(0, sampleIdx - offset);
    int posOffset = std::min(totalSamples, sampleIdx + offset);
    int winLen = posOffset - negOffset;
    if (winLen <= 0) return sampleIdx;

    int offsetCorr = (sampleIdx - offset < 0) ? std::abs(sampleIdx - offset) : 0;

    std::vector<float> oneDist(winLen, 0.0f);
    float prev = 2.0f;

    for (int i = 0; i < winLen; ++i) {
        float val = audio[negOffset + i];
        float fdist = std::abs(val);

        if (prev * val > 0.0f)
            fdist += 0.4f;
        else if (prev > 0.0f)
            fdist += 0.1f;

        prev = val;
        oneDist[i] = fdist;
    }

    int bestIdx = 0;
    float minDist = 1e30f;

    for (int i = 0; i < winLen; ++i) {
        float dist = oneDist[i];
        dist += 0.1f * std::abs(i - offset + offsetCorr) / static_cast<float>(windowSize / 2);
        if (dist < minDist) {
            minDist = dist;
            bestIdx = i;
        }
    }

    if (minDist > 0.2f) return sampleIdx;
    return sampleIdx + bestIdx - offset + offsetCorr;
}

// ---- Utility functions ----

float LoopFinder::cosineSimilarity(const float* a, const float* b, int len) {
    float dot = 0.0f, normA = 0.0f, normB = 0.0f;
    for (int i = 0; i < len; ++i) {
        dot   += a[i] * b[i];
        normA += a[i] * a[i];
        normB += b[i] * b[i];
    }
    float denom = std::sqrt(normA) * std::sqrt(normB);
    return (denom > 1e-10f) ? (dot / denom) : 0.95f;
}

float LoopFinder::dotProduct(const float* a, const float* b, int len) {
    float dot = 0.0f;
    for (int i = 0; i < len; ++i) dot += a[i] * b[i];
    return std::max(0.0f, dot);
}

float LoopFinder::vectorNorm(const float* v, int len) {
    float sum = 0.0f;
    for (int i = 0; i < len; ++i)
        sum += v[i] * v[i];
    return std::sqrt(sum);
}

void LoopFinder::geometricWeights(int len, float* weights, float start, float stop) {
    if (len <= 0) return;
    if (len == 1) { weights[0] = start; return; }
    float ratio = std::pow(stop / start, 1.0f / (len - 1));
    weights[0] = start;
    for (int i = 1; i < len; ++i)
        weights[i] = weights[i - 1] * ratio;
}

float LoopFinder::waveformContinuity(const float* signal, int signalLen,
                                      int startSample, int endSample,
                                      int windowLen) {
    if (windowLen <= 0) return 0.0f;
    if (endSample < windowLen || startSample < 0) return 0.0f;
    if (startSample + windowLen > signalLen) return 0.0f;

    float mse = 0.0f, ref = 0.0f;
    for (int i = 0; i < windowLen; ++i) {
        float a = signal[endSample - windowLen + i];   // tail before loop end
        float b = signal[startSample + i];             // head after loop start
        float diff = a - b;
        mse += diff * diff;
        ref += a * a + b * b;
    }
    mse /= windowLen;
    ref  /= (2.0f * windowLen);
    return (ref < 1e-10f) ? 0.95f : (1.0f / (1.0f + mse / ref));
}

void LoopFinder::normalizeChroma(std::vector<std::vector<float>>& chroma) {
    if (chroma.empty()) return;
    int numFrames = static_cast<int>(chroma[0].size());
    for (int t = 0; t < numFrames; ++t) {
        float norm2 = 0.0f;
        for (int c = 0; c < 12; ++c)
            norm2 += chroma[c][t] * chroma[c][t];
        if (norm2 > 1e-10f) {
            float inv = 1.0f / std::sqrt(norm2);
            for (int c = 0; c < 12; ++c)
                chroma[c][t] *= inv;
        }
    }
}

// ---- Candidate pair enumeration ----

void LoopFinder::findCandidatePairs(const std::vector<std::vector<float>>& chroma,
                                    const std::vector<std::vector<float>>& powerDB,
                                    const std::vector<int>& beats,
                                    int minLoopFrames, int maxLoopFrames,
                                    std::vector<LoopPoint>& candidates) {
    candidates.clear();
    LF_LOG("[findCandidatePairs] beats=%zu minLoop=%d maxLoop=%d", beats.size(), minLoopFrames, maxLoopFrames);
    if (beats.size() < 2) return;

    const float NOTE_DEVIATION_THRESHOLD = 0.0875f;
    const float ACCEPTABLE_LOUDNESS_DIFF   = 0.5f;

    int numBeats = static_cast<int>(beats.size());
    int numChromaFrames = chroma.empty() ? 0 : static_cast<int>(chroma[0].size());
    LF_LOG("[findCandidatePairs] chroma=%zux%d", chroma.size(), numChromaFrames);

    std::vector<float> maxPowerPerBeat(numBeats);

    int numPowerDBFrames = powerDB.empty() ? 0 : static_cast<int>(powerDB[0].size());
    LF_LOG("[findCandidatePairs] powerDB=%zux%d", powerDB.size(), numPowerDBFrames);

    for (int i = 0; i < numBeats; ++i) {
        int frame = beats[i];
        if (frame < 0 || frame >= numChromaFrames) {
            LF_LOG("[findCandidatePairs] WARN beat[%d]=%d out of chroma range [0,%d)", i, frame, numChromaFrames);
            continue;
        }
        maxPowerPerBeat[i] = 0.0f;

        if (frame >= 0 && frame < numPowerDBFrames) {
            float sum = 0.0f;
            for (size_t f = 0; f < powerDB.size(); ++f)
                sum += powerDB[f][frame];
            maxPowerPerBeat[i] = sum / powerDB.size();
        }
    }

    std::vector<float> beatChroma(static_cast<size_t>(numBeats) * 12);
    for (int i = 0; i < numBeats; ++i) {
        int frame = beats[i];
        if (frame >= 0 && frame < numChromaFrames) {
            float* dst = &beatChroma[i * 12];
            for (int c = 0; c < 12; ++c)
                dst[c] = chroma[c][frame];
        }
    }

    LF_LOG("[findCandidatePairs] noteThreshold=%.6f", NOTE_DEVIATION_THRESHOLD);

    for (int endIdx = 0; endIdx < numBeats; ++endIdx) {
        int loopEnd = beats[endIdx];
        if (loopEnd >= numChromaFrames) continue;
        float loudEnd = maxPowerPerBeat[endIdx];
        const float* chromaEnd = &beatChroma[endIdx * 12];

        for (int startIdx = endIdx - 1; startIdx >= 0; --startIdx) {
            int loopStart = beats[startIdx];
            if (loopStart >= numChromaFrames) continue;
            int loopLen = loopEnd - loopStart;

            if (loopLen > maxLoopFrames) break;
            if (loopLen < minLoopFrames) continue;

            float loudnessDiff = std::abs(loudEnd - maxPowerPerBeat[startIdx]);
            if (loudnessDiff > ACCEPTABLE_LOUDNESS_DIFF) continue;

            const float* chromaStart = &beatChroma[startIdx * 12];
            float noteDist = 0.0f;
            for (int c = 0; c < 12; ++c) {
                float diff = chromaEnd[c] - chromaStart[c];
                noteDist += diff * diff;
            }
            noteDist = std::sqrt(noteDist);

            if (noteDist > NOTE_DEVIATION_THRESHOLD) continue;

            LoopPoint lp;
            lp.loopStart    = loopStart;
            lp.loopEnd      = loopEnd;
            lp.noteDiff     = noteDist;
            lp.loudnessDiff = loudnessDiff;
            lp.score        = 0.0f;
            candidates.push_back(lp);
        }
    }
    LF_LOG("[findCandidatePairs] found %zu candidates", candidates.size());
}

// ---- Cosine similarity scoring ----

void LoopFinder::scoreCandidates(const std::vector<std::vector<float>>& chroma,
                                 float bpm, int hopSize, int sampleRate,
                                 std::vector<LoopPoint>& candidates) {
    LF_LOG("[scoreCandidates] candidates=%zu bpm=%.1f", candidates.size(), bpm);
    if (candidates.empty()) return;

    int numChromaFrames = static_cast<int>(chroma[0].size());

    float beatsPerSec = bpm / 60.0f;
    int numTestBeats = 12;
    float secondsToTest = numTestBeats / beatsPerSec;
    int testOffsetFrames = static_cast<int>(secondsToTest * sampleRate / hopSize);
    if (testOffsetFrames > numChromaFrames)
        testOffsetFrames = numChromaFrames / 4;
    if (testOffsetFrames < 2) testOffsetFrames = 2;
    LF_LOG("[scoreCandidates] testOffsetFrames=%d", testOffsetFrames);

    if (candidates.size() >= 100) {
        std::vector<float> noteVals, loudVals;
        noteVals.reserve(candidates.size());
        loudVals.reserve(candidates.size());
        for (auto& lp : candidates) {
            if (lp.noteDiff > 1e-3f) noteVals.push_back(lp.noteDiff);
            if (lp.loudnessDiff > 1e-3f) loudVals.push_back(lp.loudnessDiff);
        }
        if (!noteVals.empty() && !loudVals.empty()) {
            auto noteMid = noteVals.begin() + noteVals.size() * 3 / 4;
            std::nth_element(noteVals.begin(), noteMid, noteVals.end());
            float noteThreshold = *noteMid;

            auto loudMid = loudVals.begin() + loudVals.size() / 2;
            std::nth_element(loudVals.begin(), loudMid, loudVals.end());
            float dbThreshold = std::max(0.25f, *loudMid);

            std::vector<LoopPoint> filtered;
            filtered.reserve(candidates.size());
            for (auto& lp : candidates) {
                if (lp.loudnessDiff <= dbThreshold && lp.noteDiff <= noteThreshold)
                    filtered.push_back(lp);
            }
            candidates = std::move(filtered);
            LF_LOG("[scoreCandidates] pruned to %zu (note<%.4f loud<%.4f)",
                   candidates.size(), noteThreshold, dbThreshold);
        }
    }

    float weightStart = std::max(2.0f, static_cast<float>(testOffsetFrames) / 12.0f);

    std::vector<float> weights(testOffsetFrames);
    geometricWeights(testOffsetFrames, weights.data(), weightStart, 1.0f);

    std::vector<float> revWeights(testOffsetFrames);
    geometricWeights(testOffsetFrames, revWeights.data(), weightStart, 1.0f);
    std::reverse(revWeights.begin(), revWeights.end());

    std::vector<float> chromaA(12), chromaB(12);
    std::vector<float> lookaheadBuf(testOffsetFrames);
    std::vector<float> lookbehindBuf(testOffsetFrames);
    std::vector<float> padded(testOffsetFrames);

    for (auto& lp : candidates) {
        int b1 = static_cast<int>(lp.loopStart);
        int b2 = static_cast<int>(lp.loopEnd);

        if (b1 < 0 || b1 >= numChromaFrames || b2 < 0 || b2 >= numChromaFrames) {
            LF_LOG("[scoreCandidates] WARN b1=%d b2=%d out of [0,%d)", b1, b2, numChromaFrames);
            continue;
        }

        int b1End = std::min(b1 + testOffsetFrames, numChromaFrames);
        int b2End = std::min(b2 + testOffsetFrames, numChromaFrames);
        int maxOffset = std::min(b1End - b1, b2End - b2);

        float lookaheadScore = 0.0f;
        for (int i = 0; i < maxOffset; ++i) {
            for (int c = 0; c < 12; ++c) {
                chromaA[c] = chroma[c][b1 + i];
                chromaB[c] = chroma[c][b2 + i];
            }
            lookaheadBuf[i] = dotProduct(chromaA.data(), chromaB.data(), 12);
        }

        if (maxOffset > 0) {
            float wSum = 0.0f;
            if (maxOffset < testOffsetFrames) {
                std::fill(padded.begin(), padded.end(), 0.0f);
                for (int i = 0; i < maxOffset; ++i) padded[i] = lookaheadBuf[i];
                for (int i = 0; i < testOffsetFrames; ++i) {
                    lookaheadScore += padded[i] * weights[i];
                    wSum += weights[i];
                }
            } else {
                for (int i = 0; i < maxOffset; ++i) {
                    lookaheadScore += lookaheadBuf[i] * weights[i];
                    wSum += weights[i];
                }
            }
            lookaheadScore /= wSum;
        }

        int b1Start = std::max(0, b1 - testOffsetFrames);
        int b2Start = std::max(0, b2 - testOffsetFrames);
        int maxNegOffset = std::min(b1 - b1Start, b2 - b2Start);

        float lookbehindScore = 0.0f;
        for (int i = 0; i < maxNegOffset; ++i) {
            for (int c = 0; c < 12; ++c) {
                chromaA[c] = chroma[c][b1Start + i];
                chromaB[c] = chroma[c][b2Start + i];
            }
            lookbehindBuf[i] = dotProduct(chromaA.data(), chromaB.data(), 12);
        }

        if (maxNegOffset > 0) {
            float wSum = 0.0f;
            if (maxNegOffset < testOffsetFrames) {
                std::fill(padded.begin(), padded.end(), 0.0f);
                for (int i = 0; i < maxNegOffset; ++i) padded[i] = lookbehindBuf[i];
                for (int i = 0; i < testOffsetFrames; ++i) {
                    lookbehindScore += padded[i] * revWeights[i];
                    wSum += revWeights[i];
                }
            } else {
                for (int i = 0; i < maxNegOffset; ++i) {
                    lookbehindScore += lookbehindBuf[i] * revWeights[i];
                    wSum += revWeights[i];
                }
            }
            lookbehindScore /= wSum;
        }

        lp.score = std::max(lookaheadScore, lookbehindScore);
    }

    std::sort(candidates.begin(), candidates.end(),
              [](const LoopPoint& a, const LoopPoint& b) {
                  return a.score > b.score;
              });
    LF_LOG("[scoreCandidates] done topScore=%.4f",
           candidates.empty() ? 0.0f : candidates[0].score);
}

// ---- Duration prioritization ----

void LoopFinder::prioritizeDuration(std::vector<LoopPoint>& candidates) {
    if (candidates.size() <= 1) return;

    std::vector<float> scoreVals, loudVals;
    scoreVals.reserve(candidates.size());
    loudVals.reserve(candidates.size());
    for (auto& lp : candidates) {
        scoreVals.push_back(lp.score);
        if (lp.loudnessDiff > 1e-3f) loudVals.push_back(lp.loudnessDiff);
    }

    auto score90 = scoreVals.begin() + scoreVals.size() * 9 / 10;
    std::nth_element(scoreVals.begin(), score90, scoreVals.end());
    float scoreThreshold = std::max(*score90, candidates[0].score - 1e-4f);

    float dbThreshold = 0.25f;
    if (!loudVals.empty()) {
        auto loudMid = loudVals.begin() + loudVals.size() / 2;
        std::nth_element(loudVals.begin(), loudMid, loudVals.end());
        dbThreshold = std::max(0.25f, *loudMid);
    }

    int bestDurationIdx = 0;
    int64_t bestDuration = 0;

    for (int i = 0; i < (int)candidates.size(); ++i) {
        if (candidates[i].score < scoreThreshold) break;
        if (candidates[i].loudnessDiff > dbThreshold) continue;
        int64_t dur = candidates[i].loopEnd - candidates[i].loopStart;
        if (dur > bestDuration) {
            bestDuration = dur;
            bestDurationIdx = i;
        }
    }

    if (bestDurationIdx > 0) {
        LoopPoint best = candidates[bestDurationIdx];
        candidates.erase(candidates.begin() + bestDurationIdx);
        candidates.insert(candidates.begin(), best);
    }
}

// ---- Main analysis pipeline ----

std::vector<LoopPoint> LoopFinder::analyze(const float* monoSignal, int signalLen,
                                           int sampleRate, const Config& config) {
    std::vector<LoopPoint> results;
    LF_LOG("[analyze] signalLen=%d sampleRate=%d nFFT=%d hopSize=%d",
           signalLen, sampleRate, config.nFFT, config.hopSize);

    auto t0 = std::chrono::high_resolution_clock::now();
    auto t1 = t0;

    // 1. STFT
    LF_LOG("[analyze] step 1: STFT init");
    STFT stft;
    if (!stft.init(config.nFFT, config.hopSize)) {
        fprintf(stderr, "[loopfinder] ERROR: STFT init failed\n");
        return results;
    }

    std::vector<std::vector<float>> powerSpec;
    int numFreqBins, numFrames;
    stft.computePower(monoSignal, signalLen, powerSpec, numFreqBins, numFrames);
    LF_LOG("[analyze] step 1 done: freqBins=%d frames=%d", numFreqBins, numFrames);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("STFT", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 2. HPSS
    LF_LOG("[analyze] step 2: HPSS");
    HPSS hpss;
    std::vector<std::vector<float>> harmonicSpec;
    hpss.harmonicOnly(powerSpec, harmonicSpec);
    LF_LOG("[analyze] step 2 done: harmonic %zux%zu", harmonicSpec.size(),
           harmonicSpec.empty() ? 0 : harmonicSpec[0].size());
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("HPSS", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 3. Chroma
    LF_LOG("[analyze] step 3: Chroma");
    ChromaExtractor chromaExtractor;
    std::vector<std::vector<float>> chromagram;
    chromaExtractor.extract(harmonicSpec, sampleRate, config.nFFT, chromagram);
    LF_LOG("[analyze] step 3 done: chroma %zux%zu", chromagram.size(),
           chromagram.empty() ? 0 : chromagram[0].size());
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("Chroma", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 4. Power DB
    LF_LOG("[analyze] step 4: powerDB");
    std::vector<std::vector<float>> powerDB(numFreqBins, std::vector<float>(numFrames, 0.0f));
    {
        std::vector<float> allVals;
        allVals.reserve(static_cast<size_t>(numFreqBins) * numFrames);
        for (int f = 0; f < numFreqBins; ++f)
            for (int t = 0; t < numFrames; ++t)
                if (powerSpec[f][t] > 1e-10f)
                    allVals.push_back(powerSpec[f][t]);
        LF_LOG("[analyze] step 4: median over %zu vals", allVals.size());

        float medianRef = 1.0f;
        if (!allVals.empty()) {
            auto mid = allVals.begin() + allVals.size() / 2;
            std::nth_element(allVals.begin(), mid, allVals.end());
            medianRef = *mid;
        }
        LF_LOG("[analyze] step 4: median=%.6f", medianRef);

        const float logScale = 10.0f / std::log(10.0f);
        const float logRef   = std::log(std::max(medianRef, 1e-10f));

        std::vector<float> aWeights(numFreqBins, 0.0f);
        for (int k = 1; k < numFreqBins; ++k) {
            float freq = (k * sampleRate) / static_cast<float>(config.nFFT);
            float f2 = freq * freq;
            float numA = 12194.0f * 12194.0f * f2 * f2;
            float denA = (f2 + 20.6f * 20.6f) *
                         std::sqrt((f2 + 107.7f * 107.7f) * (f2 + 737.9f * 737.9f)) *
                         (f2 + 12194.0f * 12194.0f);
            aWeights[k] = (denA > 0) ? (numA / denA) : 0.0f;
        }

        for (int k = 0; k < numFreqBins; ++k) {
            if (k == 0) {
                for (int t = 0; t < numFrames; ++t)
                    powerDB[k][t] = 0.0f;
                continue;
            }
            float aw = aWeights[k];
            for (int t = 0; t < numFrames; ++t) {
                float val = powerSpec[k][t] * aw;
                powerDB[k][t] = logScale * (std::log(std::max(val, 1e-10f)) - logRef);
            }
        }
    }
    LF_LOG("[analyze] step 4 done");
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("PowerDB", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 5. Beat detection
    LF_LOG("[analyze] step 5: beat detection");
    BeatDetector beatDet;
    std::vector<int> beatFrames;
    float bpm = 120.0f;
    if (beatDet.init(config.hopSize, sampleRate)) {
        beatDet.detect(monoSignal, signalLen, beatFrames, bpm);
    } else {
        fprintf(stderr, "[loopfinder] WARN: aubio init failed, sampling pseudo-beats\n");
        int step = std::max(1, numFrames / 200);
        for (int i = 0; i < numFrames; i += step)
            beatFrames.push_back(i);
    }
    std::sort(beatFrames.begin(), beatFrames.end());
    beatFrames.erase(std::unique(beatFrames.begin(), beatFrames.end()), beatFrames.end());

    {
        std::vector<int> spacing;
        for (size_t i = 1; i < beatFrames.size(); ++i)
            spacing.push_back(beatFrames[i] - beatFrames[i - 1]);
        if (!spacing.empty()) {
            std::sort(spacing.begin(), spacing.end());
            fprintf(stderr, "[loopfinder] beat spacing: min=%d med=%d max=%d count=%zu\n",
                spacing.front(), spacing[spacing.size() / 2], spacing.back(), spacing.size());
        }
    }

    int frameStep = std::max(4, config.hopSize / 32);
    for (int i = 0; i < numFrames; i += frameStep)
        beatFrames.push_back(i);

    int nBefore = static_cast<int>(beatFrames.size());
    for (int i = 1; i < nBefore; ++i) {
        int gap = beatFrames[i] - beatFrames[i - 1];
        if (gap >= 8) {
            beatFrames.push_back(beatFrames[i - 1] + gap / 2);
        }
    }

    std::sort(beatFrames.begin(), beatFrames.end());
    beatFrames.erase(std::unique(beatFrames.begin(), beatFrames.end()), beatFrames.end());
    LF_LOG("[analyze] step 5 done: beats=%zu bpm=%.1f", beatFrames.size(), bpm);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("BeatDetect", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 6. Loop duration constraints
    int totalFrames = stft.getNumFrames(signalLen);
    int minLoopFrames = static_cast<int>(config.minDurationMultiplier * totalFrames);
    if (config.minLoopDurationSec > 0.0f) {
        minLoopFrames = static_cast<int>(config.minLoopDurationSec * sampleRate / config.hopSize);
    }
    minLoopFrames = std::max(1, minLoopFrames);

    int maxLoopFrames = totalFrames;
    if (config.maxLoopDurationSec > 0.0f) {
        maxLoopFrames = static_cast<int>(config.maxLoopDurationSec * sampleRate / config.hopSize);
    }
    LF_LOG("[analyze] step 6: minLoopFrames=%d maxLoopFrames=%d", minLoopFrames, maxLoopFrames);

    // 7. Find candidate pairs
    LF_LOG("[analyze] step 7: findCandidatePairs");
    std::vector<LoopPoint> candidates;
    findCandidatePairs(chromagram, powerDB, beatFrames,
                       minLoopFrames, maxLoopFrames, candidates);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("findCandidates", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    if (candidates.empty()) {
        fprintf(stderr, "[loopfinder] no loop candidates found\n");
        return results;
    }

    // 8. Score candidates
    LF_LOG("[analyze] step 7.5: normalizeChroma");
    normalizeChroma(chromagram);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("normalizeChroma", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    LF_LOG("[analyze] step 8: scoreCandidates");
    scoreCandidates(chromagram, bpm, config.hopSize, sampleRate, candidates);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("scoreCandidates", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    // 9. Prioritize longer loops
    LF_LOG("[analyze] step 9: prioritizeDuration");
    prioritizeDuration(candidates);
    t1 = std::chrono::high_resolution_clock::now();
    LF_PERF("prioritizeDuration", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());

    // 10. Convert frame indices to sample indices
    for (auto& lp : candidates) {
        lp.loopStart = static_cast<int64_t>(lp.loopStart) * config.hopSize;
        lp.loopEnd   = static_cast<int64_t>(lp.loopEnd) * config.hopSize;
        lp.loopStart = nearestZeroCrossing(monoSignal, signalLen, sampleRate,
                                            static_cast<int>(lp.loopStart));
        lp.loopEnd   = nearestZeroCrossing(monoSignal, signalLen, sampleRate,
                                            static_cast<int>(lp.loopEnd));
    }

    // 11. Return top N
    int topN = std::min(config.topN, static_cast<int>(candidates.size()));
    results.assign(candidates.begin(), candidates.begin() + topN);
    LF_LOG("[analyze] DONE topN=%d topScore=%.4f", topN,
           results.empty() ? 0.0f : results[0].score);
    return results;
}

} // namespace loopfinder
