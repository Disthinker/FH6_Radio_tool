#pragma once

#include <vector>

namespace loopfinder {

class HPSS {
public:
    void separate(const std::vector<std::vector<float>>& powerSpec,
                  std::vector<std::vector<float>>& harmonic,
                  std::vector<std::vector<float>>& percussive,
                  int kernelSize = 31);

    void harmonicOnly(std::vector<std::vector<float>>& powerSpec,
                      std::vector<std::vector<float>>& harmonic,
                      int kernelSize = 31);
};

} // namespace loopfinder
