cmake_minimum_required(VERSION 3.14)
project(loopfinder VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 99)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(BUILD_FOR_ANDROID   "Build for Android NDK (cross-compile)" OFF)
option(BUILD_TEST           "Build test executable" OFF)
option(LOOPFINDER_DEBUG_LOG "Enable internal debug logs" OFF)

# ============ dr_libs (header-only) ============
add_library(dr_libs INTERFACE)
target_include_directories(dr_libs INTERFACE ${CMAKE_SOURCE_DIR}/third_party)

# ============ stb_vorbis ============
add_library(stb_vorbis STATIC ${CMAKE_SOURCE_DIR}/third_party/stb_vorbis_impl.c)
target_include_directories(stb_vorbis PUBLIC ${CMAKE_SOURCE_DIR}/third_party)

# ============ KissFFT ============
add_library(kissfft STATIC
    ${CMAKE_SOURCE_DIR}/third_party/kiss_fft.c
    ${CMAKE_SOURCE_DIR}/third_party/kiss_fftr.c
)
target_include_directories(kissfft PUBLIC ${CMAKE_SOURCE_DIR}/third_party)

# ============ aubio ============
set(AUBIO_SRC_DIR ${CMAKE_SOURCE_DIR}/third_party/aubio/src)

file(GLOB_RECURSE AUBIO_SOURCES ${AUBIO_SRC_DIR}/*.c)

add_library(aubio STATIC ${AUBIO_SOURCES})

target_include_directories(aubio PUBLIC ${AUBIO_SRC_DIR})

target_compile_definitions(aubio PUBLIC
    HAVE_CONFIG_H
    HAVE_STDLIB_H
    HAVE_STDIO_H
    HAVE_MATH_H
    HAVE_STRING_H
    HAVE_LIMITS_H
    HAVE_STDARG_H
    HAVE_C99_VARARGS_MACROS
)

if(NOT MSVC AND NOT APPLE)
    target_link_libraries(aubio PRIVATE m)
endif()

if(MSVC)
    target_compile_options(aubio PRIVATE /W0)
else()
    target_compile_options(aubio PRIVATE -w)
endif()

# ============ loopfinder ============
set(LOOPFINDER_SRC
    src/audio_decoder.cpp
    src/stft.cpp
    src/hpss.cpp
    src/chroma.cpp
    src/beat_detector.cpp
    src/loop_finder.cpp
    src/loopfinder_api.cpp
)

if(BUILD_FOR_ANDROID)
    list(APPEND LOOPFINDER_SRC jni/jni_main.cpp)
endif()

add_library(loopfinder SHARED ${LOOPFINDER_SRC})

set_target_properties(loopfinder PROPERTIES
    WINDOWS_EXPORT_ALL_SYMBOLS ON
)

target_include_directories(loopfinder PRIVATE
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/third_party
    ${AUBIO_SRC_DIR}
)

target_compile_definitions(loopfinder PRIVATE LOOPFINDER_EXPORTS)

target_link_libraries(loopfinder PRIVATE
    dr_libs
    stb_vorbis
    kissfft
    aubio
)

find_package(OpenMP)
if(OpenMP_CXX_FOUND)
    target_link_libraries(loopfinder PRIVATE OpenMP::OpenMP_CXX)
endif()

if(BUILD_FOR_ANDROID)
    find_library(log-lib log)
    target_link_libraries(loopfinder PRIVATE ${log-lib})
endif()

if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    target_compile_options(loopfinder PRIVATE
        /W3
        $<$<CONFIG:Debug>:/Od /Zi>
        $<$<CONFIG:Release>:/O2>
    )
    target_compile_definitions(loopfinder PRIVATE
        $<$<CONFIG:Debug>:_DEBUG>
        $<$<CONFIG:Release>:NDEBUG>
    )
else()
    target_compile_options(loopfinder PRIVATE -Wall -Wextra -O3 -ffast-math)
endif()

if(LOOPFINDER_DEBUG_LOG)
    target_compile_definitions(loopfinder PRIVATE LOOPFINDER_DEBUG)
endif()

if(BUILD_TEST)
    add_executable(loopfinder_test test/test_main.cpp)
    target_include_directories(loopfinder_test PRIVATE ${CMAKE_SOURCE_DIR}/include)
    target_link_libraries(loopfinder_test PRIVATE loopfinder)
endif()
