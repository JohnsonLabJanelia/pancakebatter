// camera_discover.cpp: read-only eSDK discovery. Prints every Emergent camera the host can see
// as JSON, including which host NIC each camera answered on (that is "where it is plugged in").
//
// Build (see build_camera_tools.sh):
//   g++ -O2 -I/opt/EVT/eSDK/include camera_discover.cpp -o camera_discover \
//       -L/opt/EVT/eSDK/lib -lEmergentCamera -lEmergentGenICam -lEmergentGigEVision -Wl,-rpath,/opt/EVT/eSDK/lib
//
// Usage: camera_discover [--if IFNAME] [--timeout-ms N] [--broadcast]
// Sends only GigE Vision discovery packets; it never opens a camera or changes anything.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

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

int main(int argc, char** argv) {
    ListDevicesSettings settings;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--if") && i + 1 < argc) settings.ifName = argv[++i];
        else if (!strcmp(argv[i], "--timeout-ms") && i + 1 < argc) settings.timeout = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--broadcast")) settings.broadcast = 1;
        else { fprintf(stderr, "usage: %s [--if IFNAME] [--timeout-ms N] [--broadcast]\n", argv[0]); return 2; }
    }

    const unsigned int kMax = 64;
    static GigEVisionDeviceInfo info[kMax];
    unsigned int bufSize = kMax, actual = 0;
    EVT_ERROR err = EVT_ListDevices(info, &bufSize, &actual, &settings);
    if (err != EVT_SUCCESS) {
        fprintf(stderr, "EVT_ListDevices failed: %d\n", (int)err);
        return 1;
    }

    printf("{\"devices\": [");
    for (unsigned int i = 0; i < bufSize && i < actual; ++i) {
        const GigEVisionDeviceInfo& d = info[i];
        printf("%s\n  {\"mac\": \"%s\", \"serial\": \"%s\", \"model\": \"%s\", \"manufacturer\": \"%s\", "
               "\"user_name\": \"%s\", \"device_version\": \"%s\", "
               "\"ip\": \"%s\", \"mask\": \"%s\", \"gateway\": \"%s\", "
               "\"persistent_ip_supported\": %s, \"persistent_ip_active\": %s, \"dhcp_active\": %s, "
               "\"nic\": {\"name\": \"%s\", \"ip\": \"%s\", \"mac\": \"%s\"}}",
               i ? "," : "",
               json_escape(d.macAddress).c_str(), json_escape(d.serialNumber).c_str(), json_escape(d.modelName).c_str(),
               json_escape(d.manufacturerName).c_str(), json_escape(d.userDefinedName).c_str(),
               json_escape(d.deviceVersion).c_str(),
               json_escape(d.currentIp).c_str(), json_escape(d.currentSubnetMask).c_str(),
               json_escape(d.defaultGateway).c_str(),
               IS_PERSISTENT_IP_SUPPORTED(d.ipConfigOptions) ? "true" : "false",
               IS_PERSISTENT_IP_ACTIVATED(d.ipConfigCurrent) ? "true" : "false",
               IS_DHCP_ACTIVATED(d.ipConfigCurrent) ? "true" : "false",
               json_escape(d.nic.friendlyName).c_str(), json_escape(d.nic.ip4Address).c_str(),
               json_escape(d.nic.macAddress).c_str());
    }
    printf("\n]}\n");
    return 0;
}
