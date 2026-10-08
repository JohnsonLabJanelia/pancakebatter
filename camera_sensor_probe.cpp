// camera_sensor_probe.cpp: open one Emergent camera (control channel only, no streaming) and print
// the sensor facts a host inventory wants as JSON: maximum width/height, the sensor's own size when
// the camera reports it, and the list of pixel formats. Used by camera_adopt.py to fill
// cameras.<mac>.sensor.{x_resolution,y_resolution,monochrome_modes,raw_modes}. Reads only; it sets
// nothing on the camera. Do not run it against a camera that Orange is streaming from: the open
// takes the control channel.
//
// Build (camera_adopt.py does this on first use, into build/):
//   g++ -O2 -I/opt/EVT/eSDK/include camera_sensor_probe.cpp -o camera_sensor_probe \
//       -L/opt/EVT/eSDK/lib -lEmergentCamera -lEmergentGenICam -lEmergentGigEVision -Wl,-rpath,/opt/EVT/eSDK/lib
//
// Usage: camera_sensor_probe --serial N [--if IFNAME] [--timeout-ms N] [--broadcast]
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <EmergentCameraAPIs.h>
#include <gigevisiondeviceinfo.h>

using namespace Emergent;

static std::string json_escape(const char* s) {
    std::string out;
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { out += '\\'; out += (char)c; }
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); out += b; }
        else out += (char)c;
    }
    return out;
}

static void print_uint(const char* key, bool ok, unsigned int v, bool& first) {
    if (!ok) return;
    printf("%s\"%s\": %u", first ? "" : ", ", key, v);
    first = false;
}

int main(int argc, char** argv) {
    const char* serial = NULL;
    ListDevicesSettings settings;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--serial") && i + 1 < argc) serial = argv[++i];
        else if (!strcmp(argv[i], "--if") && i + 1 < argc) settings.ifName = argv[++i];
        else if (!strcmp(argv[i], "--timeout-ms") && i + 1 < argc) settings.timeout = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--broadcast")) settings.broadcast = 1;
        else { fprintf(stderr, "usage: %s --serial N [--if IFNAME] [--timeout-ms N] [--broadcast]\n", argv[0]); return 2; }
    }
    if (!serial) { fprintf(stderr, "--serial is required\n"); return 2; }

    const unsigned int kMax = 64;
    static GigEVisionDeviceInfo info[kMax];
    unsigned int bufSize = kMax, actual = 0;
    EVT_ERROR err = EVT_ListDevices(info, &bufSize, &actual, &settings);
    if (err != EVT_SUCCESS) { fprintf(stderr, "EVT_ListDevices failed: %d\n", (int)err); return 1; }
    const GigEVisionDeviceInfo* dev = NULL;
    for (unsigned int i = 0; i < bufSize && i < actual; ++i)
        if (!strcmp(info[i].serialNumber, serial)) dev = &info[i];
    if (!dev) {
        fprintf(stderr, "camera %s not found; saw %u device(s):", serial, actual < bufSize ? actual : bufSize);
        for (unsigned int i = 0; i < bufSize && i < actual; ++i) fprintf(stderr, " %s@%s", info[i].serialNumber, info[i].currentIp);
        fprintf(stderr, "\n");
        return 3;
    }

    CEmergentCamera camera;
    err = EVT_CameraOpen(&camera, dev);
    if (err != EVT_SUCCESS) { fprintf(stderr, "EVT_CameraOpen failed: %d (in use by another program?)\n", (int)err); return 4; }

    unsigned int wmax = 0, hmax = 0, sw = 0, sh = 0;
    bool okw = EVT_CameraGetUInt32ParamMax(&camera, "Width", &wmax) == EVT_SUCCESS;
    bool okh = EVT_CameraGetUInt32ParamMax(&camera, "Height", &hmax) == EVT_SUCCESS;
    bool oksw = EVT_CameraGetUInt32Param(&camera, "SensorWidth", &sw) == EVT_SUCCESS;
    bool oksh = EVT_CameraGetUInt32Param(&camera, "SensorHeight", &sh) == EVT_SUCCESS;
    char formats[4096] = {0};
    unsigned long used = 0;
    bool okf = EVT_CameraGetEnumParamRange(&camera, "PixelFormat", formats, sizeof formats, &used) == EVT_SUCCESS;
    EVT_CameraClose(&camera);

    printf("{\"serial\": \"%s\", \"model\": \"%s\", ", json_escape(dev->serialNumber).c_str(), json_escape(dev->modelName).c_str());
    bool first = true;
    print_uint("width_max", okw, wmax, first);
    print_uint("height_max", okh, hmax, first);
    print_uint("sensor_width", oksw, sw, first);
    print_uint("sensor_height", oksh, sh, first);
    printf("%s\"pixel_formats\": [", first ? "" : ", ");
    if (okf) {
        // the SDK returns a comma-separated list
        std::string list(formats);
        size_t start = 0; bool firstf = true;
        while (start <= list.size()) {
            size_t comma = list.find(',', start);
            std::string item = list.substr(start, comma == std::string::npos ? std::string::npos : comma - start);
            while (!item.empty() && (item.front() == ' ')) item.erase(0, 1);
            while (!item.empty() && (item.back() == ' ')) item.pop_back();
            if (!item.empty()) { printf("%s\"%s\"", firstf ? "" : ", ", json_escape(item.c_str()).c_str()); firstf = false; }
            if (comma == std::string::npos) break;
            start = comma + 1;
        }
    }
    printf("]}\n");
    return 0;
}
