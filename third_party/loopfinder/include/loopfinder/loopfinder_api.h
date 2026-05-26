#pragma once

#include <stdint.h>

// ---- Windows DLL export / import ----
#ifdef _WIN32
  #ifdef LOOPFINDER_EXPORTS
    #define LOOPFINDER_API __declspec(dllexport)
  #else
    #define LOOPFINDER_API __declspec(dllimport)
  #endif
#else
  #define LOOPFINDER_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int64_t loopStart;
    int64_t loopEnd;
    float   noteDiff;
    float   loudnessDiff;
    float   score;
} lf_loop_point_t;

LOOPFINDER_API int lf_analyze_file(
    const char* filepath,
    int topN,
    lf_loop_point_t* outPoints,
    int capacity
);

LOOPFINDER_API const char* lf_get_last_error();

#ifdef __cplusplus
}
#endif
