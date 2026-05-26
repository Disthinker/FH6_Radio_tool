#include <jni.h>
#include <string>
#include <vector>

#include "loopfinder/loopfinder_api.h"

extern "C" {

JNIEXPORT jobjectArray JNICALL
Java_com_cpu_seamlessloopmobile_jni_NativeAudio_analyzeLoopPoints(
    JNIEnv* env, jclass /*clazz*/, jstring filePath, jint topN)
{
    // 1. Get file path from jstring
    const char* pathCStr = env->GetStringUTFChars(filePath, nullptr);
    if (!pathCStr) return nullptr;
    std::string path(pathCStr);
    env->ReleaseStringUTFChars(filePath, pathCStr);

    // 2. Call the C API — handles decode + analyze + trim offset in one call
    int capacity = std::max(1, static_cast<int>(topN));
    std::vector<lf_loop_point_t> points(static_cast<size_t>(capacity));

    int count = lf_analyze_file(path.c_str(), topN, points.data(), capacity);
    if (count <= 0) {
        // Report error (best effort) and return null
        const char* err = lf_get_last_error();
        if (err && err[0]) {
            jclass exClass = env->FindClass("java/lang/RuntimeException");
            if (exClass) env->ThrowNew(exClass, err);
        }
        return nullptr;
    }

    // 3. Find LoopPoint Java class and constructor (JJFFF)V
    jclass loopPointClass = env->FindClass("com/cpu/seamlessloopmobile/jni/LoopPoint");
    if (!loopPointClass) return nullptr;

    jmethodID constructor = env->GetMethodID(loopPointClass, "<init>", "(JJFFF)V");
    if (!constructor) return nullptr;

    // 4. Create Java array of LoopPoint
    jobjectArray result = env->NewObjectArray(count, loopPointClass, nullptr);
    if (!result) return nullptr;

    for (int i = 0; i < count; ++i) {
        jobject lpObj = env->NewObject(
            loopPointClass, constructor,
            static_cast<jlong>(points[i].loopStart),
            static_cast<jlong>(points[i].loopEnd),
            static_cast<jfloat>(points[i].noteDiff),
            static_cast<jfloat>(points[i].loudnessDiff),
            static_cast<jfloat>(points[i].score));
        env->SetObjectArrayElement(result, i, lpObj);
        env->DeleteLocalRef(lpObj);
    }

    return result;
}

} // extern "C"
