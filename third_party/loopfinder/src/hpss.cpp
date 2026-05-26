#include "loopfinder/hpss.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <vector>

#ifdef _OPENMP
#include <omp.h>
#endif

#define HPSS_PERF(step, ms) \
    fprintf(stderr, "[loopfinder hpss] %s: %lld ms\n", step, (long long)(ms))

namespace loopfinder {

class SlidingMedian {
public:
    explicit SlidingMedian(int capacity) : buf_(capacity), count_(0) {}

    void clear() { count_ = 0; }

    void add(float val) {
        auto it = std::lower_bound(buf_.begin(), buf_.begin() + count_, val);
        int pos = static_cast<int>(it - buf_.begin());
        for (int i = count_; i > pos; --i) buf_[i] = buf_[i - 1];
        buf_[pos] = val;
        ++count_;
    }

    void remove(float val) {
        auto it = std::find(buf_.begin(), buf_.begin() + count_, val);
        int pos = static_cast<int>(it - buf_.begin());
        if (pos < count_) {
            for (int i = pos; i < count_ - 1; ++i) buf_[i] = buf_[i + 1];
            --count_;
        }
    }

    float median() const { return buf_[count_ / 2]; }

    int count() const { return count_; }

private:
    std::vector<float> buf_;
    int count_;
};

static void medianFilterHorizontal(const float* src, float* dst,
                                    int numRows, int numCols,
                                    int halfKernel) {
    int kW = 2 * halfKernel + 1;

#ifdef _OPENMP
    #pragma omp parallel
    {
        SlidingMedian slide(kW);
        #pragma omp for schedule(static)
        for (int r = 0; r < numRows; ++r) {
            const float* srcRow = src + r * numCols;
            float*       dstRow = dst + r * numCols;
            slide.clear();
            int prevStart = 0, prevEnd = 0;

            for (int c = 0; c < numCols; ++c) {
                int cStart = std::max(0, c - halfKernel);
                int cEnd   = std::min(numCols, c + halfKernel + 1);

                for (int cc = prevStart; cc < cStart; ++cc)
                    slide.remove(srcRow[cc]);
                for (int cc = prevEnd; cc < cEnd; ++cc)
                    slide.add(srcRow[cc]);

                dstRow[c] = slide.median();
                prevStart = cStart;
                prevEnd   = cEnd;
            }
        }
    }
#else
    SlidingMedian slide(kW);
    for (int r = 0; r < numRows; ++r) {
        const float* srcRow = src + r * numCols;
        float*       dstRow = dst + r * numCols;
        slide.clear();
        int prevStart = 0, prevEnd = 0;

        for (int c = 0; c < numCols; ++c) {
            int cStart = std::max(0, c - halfKernel);
            int cEnd   = std::min(numCols, c + halfKernel + 1);

            for (int cc = prevStart; cc < cStart; ++cc)
                slide.remove(srcRow[cc]);
            for (int cc = prevEnd; cc < cEnd; ++cc)
                slide.add(srcRow[cc]);

            dstRow[c] = slide.median();
            prevStart = cStart;
            prevEnd   = cEnd;
        }
    }
#endif
}

static void medianFilterVerticalViaTranspose(const float* src, float* dst,
                                              int numFreqBins, int numFrames,
                                              int halfKernel) {
    int total = numFreqBins * numFrames;

    std::vector<float> transposed(static_cast<size_t>(total));
#ifdef _OPENMP
    #pragma omp parallel for collapse(2) schedule(static)
    for (int f = 0; f < numFreqBins; ++f)
        for (int t = 0; t < numFrames; ++t)
            transposed[t * numFreqBins + f] = src[f * numFrames + t];
#else
    for (int f = 0; f < numFreqBins; ++f)
        for (int t = 0; t < numFrames; ++t)
            transposed[t * numFreqBins + f] = src[f * numFrames + t];
#endif

    std::vector<float> transposedOut(static_cast<size_t>(total));
    medianFilterHorizontal(transposed.data(), transposedOut.data(),
                           numFrames, numFreqBins, halfKernel);

#ifdef _OPENMP
    #pragma omp parallel for collapse(2) schedule(static)
    for (int f = 0; f < numFreqBins; ++f)
        for (int t = 0; t < numFrames; ++t)
            dst[f * numFrames + t] = transposedOut[t * numFreqBins + f];
#else
    for (int f = 0; f < numFreqBins; ++f)
        for (int t = 0; t < numFrames; ++t)
            dst[f * numFrames + t] = transposedOut[t * numFreqBins + f];
#endif
}

static void applySoftMask(const std::vector<std::vector<float>>& powerSpec,
                           const float* hRaw, const float* pRaw,
                           int numFreqBins, int numFrames,
                           std::vector<std::vector<float>>& harmonic,
                           std::vector<std::vector<float>>* percussive) {
#ifdef _OPENMP
    #pragma omp parallel for schedule(static)
#endif
    for (int f = 0; f < numFreqBins; ++f) {
        float* hDst = harmonic[f].data();
        float* pDst = percussive ? (*percussive)[f].data() : nullptr;
        const float* srcRow = powerSpec[f].data();
        const float* hRow = hRaw + f * numFrames;
        const float* pRow = pRaw + f * numFrames;
        for (int t = 0; t < numFrames; ++t) {
            float h = hRow[t];
            float p = pRow[t];
            float denom = h * h + p * p;
            if (denom > 1e-12f) {
                hDst[t] = (h * h / denom) * srcRow[t];
                if (pDst) pDst[t] = (p * p / denom) * srcRow[t];
            }
        }
    }
}

void HPSS::separate(const std::vector<std::vector<float>>& powerSpec,
                    std::vector<std::vector<float>>& harmonic,
                    std::vector<std::vector<float>>& percussive,
                    int kernelSize) {
    int numFreqBins = static_cast<int>(powerSpec.size());
    int numFrames   = powerSpec.empty() ? 0 : static_cast<int>(powerSpec[0].size());
    if (numFreqBins == 0 || numFrames == 0) return;

    int halfKernel = kernelSize / 2;
    int totalCells = numFreqBins * numFrames;

    auto t0 = std::chrono::high_resolution_clock::now();
    auto t1 = t0;

    std::vector<float> srcFlat(static_cast<size_t>(totalCells));
    for (int f = 0; f < numFreqBins; ++f) {
        const float* row = powerSpec[f].data();
        float* dstRow = srcFlat.data() + f * numFrames;
        for (int t = 0; t < numFrames; ++t)
            dstRow[t] = row[t];
    }
    t1 = std::chrono::high_resolution_clock::now();
    HPSS_PERF("flatten", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    std::vector<float> hRaw(totalCells);
    medianFilterHorizontal(srcFlat.data(), hRaw.data(), numFreqBins, numFrames, halfKernel);
    t1 = std::chrono::high_resolution_clock::now();
    HPSS_PERF("horiz median", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    std::vector<float> pRaw(totalCells);
    medianFilterVerticalViaTranspose(srcFlat.data(), pRaw.data(), numFreqBins, numFrames, halfKernel);
    t1 = std::chrono::high_resolution_clock::now();
    HPSS_PERF("vert via transpose", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
    t0 = t1;

    harmonic.assign(numFreqBins, std::vector<float>(numFrames, 0.0f));
    percussive.assign(numFreqBins, std::vector<float>(numFrames, 0.0f));

    applySoftMask(powerSpec, hRaw.data(), pRaw.data(),
                  numFreqBins, numFrames, harmonic, &percussive);
    t1 = std::chrono::high_resolution_clock::now();
    HPSS_PERF("softmask", std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());
}

void HPSS::harmonicOnly(std::vector<std::vector<float>>& powerSpec,
                        std::vector<std::vector<float>>& harmonic,
                        int kernelSize) {
    int numFreqBins = static_cast<int>(powerSpec.size());
    int numFrames   = powerSpec.empty() ? 0 : static_cast<int>(powerSpec[0].size());
    if (numFreqBins == 0 || numFrames == 0) return;

    int halfKernel = kernelSize / 2;
    int totalCells = numFreqBins * numFrames;

    std::vector<float> srcFlat(static_cast<size_t>(totalCells));
    for (int f = 0; f < numFreqBins; ++f) {
        const float* row = powerSpec[f].data();
        float* dstRow = srcFlat.data() + f * numFrames;
        for (int t = 0; t < numFrames; ++t)
            dstRow[t] = row[t];
    }

    std::vector<float> hRaw(totalCells);
    medianFilterHorizontal(srcFlat.data(), hRaw.data(), numFreqBins, numFrames, halfKernel);

    std::vector<float> pRaw(totalCells);
    medianFilterVerticalViaTranspose(srcFlat.data(), pRaw.data(), numFreqBins, numFrames, halfKernel);

    harmonic.assign(numFreqBins, std::vector<float>(numFrames, 0.0f));

    applySoftMask(powerSpec, hRaw.data(), pRaw.data(),
                  numFreqBins, numFrames, harmonic, nullptr);
}}
