#include <stdio.h>
#include <string.h>
#include <vector> // Can still use vector if preferred, but not directly with C API list function

// Include the main eSDK header files
// #include <EmergentCameraAPIs.h> // Not strictly needed if only using C API
// #include <EmergentCamera.h>     // Not needed
#include <gigevisiondeviceinfo.h> // Still needed for the LabviewDeviceInfo struct definition inside EmergentCameraC.h
#include <emergenterrors.h>
#include <EmergentCameraC.h>      // Must include the C header for C API

int main() {
    const char* targetSerialNumber = "710039"; // Serial number from your YAML

    printf("--- eSDK Script: Get MAC Address by Serial Number (Using C API) ---\n");
    printf("Target Serial Number: %s\n", targetSerialNumber);

    // -----------------------------------------------------
    // 1. List Devices using the C API function
    // -----------------------------------------------------
    printf("\n1. Listing devices...\n");
    LabviewDeviceSet deviceSet = {0, 0}; // Initialize the C API device set struct
    EVT_ERROR err = EVT_ListDevices(&deviceSet); // Call the C API version

    if (err != EVT_SUCCESS) {
        fprintf(stderr, "[ERROR] Failed to list devices. Error code: %d\n", err);
        return 1;
    }

    if (deviceSet.countDevice == 0) {
        printf("No Emergent cameras found on the network.\n");
        // Cleanup is important even if no devices are found, in case resources were partially allocated.
        EVT_DeviceInfoSetCleanup(&deviceSet);
        return 1;
    }

    printf("   Found %d devices.\n", deviceSet.countDevice);


    // -----------------------------------------------------
    // 2. Find Target Camera and Get MAC Address using C API
    // -----------------------------------------------------
    printf("\n2. Searching for camera %s...\n", targetSerialNumber);
    bool found = false;
    LabviewDeviceInfo foundDeviceInfo; // To store info of the found device

    for (int i = 0; i < deviceSet.countDevice; ++i) {
        LabviewDeviceInfo currentDeviceInfo;
        // Use EVT_GetDeviceInfo to get info for the device at index 'i'
        err = EVT_GetDeviceInfo(&deviceSet, &currentDeviceInfo, i);
        if (err != EVT_SUCCESS) {
             fprintf(stderr, "[WARN] Failed to get device info for index %d. Error code: %d\n", i, err);
             continue; // Skip to next device
        }

        printf("   Checking device %d: SN='%s', MAC='%s', IP='%s'\n",
               i,
               currentDeviceInfo.serialNumber,
               currentDeviceInfo.macAddress,
               currentDeviceInfo.currentIp);

        // Compare the serial number
        if (strcmp(currentDeviceInfo.serialNumber, targetSerialNumber) == 0) {
            printf("\n   >>> Found target camera! <<<\n");
            // --- Store the MAC Address ---
            foundDeviceInfo = currentDeviceInfo; // Copy the struct
            // -----------------------------
            found = true;
            break; // Exit loop once found
        }
    }

    // -----------------------------------------------------
    // 3. Cleanup the Device List Resources
    // -----------------------------------------------------
    // IMPORTANT: Always call cleanup after EVT_ListDevices (& optionally EVT_GetDeviceInfo)
    EVT_DeviceInfoSetCleanup(&deviceSet);
    printf("\n3. Cleaned up device list resources.\n");

    // -----------------------------------------------------
    // 4. Print Result
    // -----------------------------------------------------
    if (!found) {
        fprintf(stderr, "[ERROR] Camera with serial number %s not found.\n", targetSerialNumber);
        return 1;
    } else {
         printf("\n4. Retrieved Information:\n");
         printf("   Target Serial Number: %s\n", foundDeviceInfo.serialNumber);
         printf("   MAC Address: %s\n", foundDeviceInfo.macAddress); // Print the stored MAC
    }


    printf("\n--- Script Finished ---\n");
    return 0;
}