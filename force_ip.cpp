#include <stdio.h>
#include <string.h>
#include <vector>

// Include the main eSDK header files
#include <EmergentCameraAPIs.h>
#include <EmergentCamera.h>
#include <gigevisiondeviceinfo.h>
#include <emergenterrors.h>
#include <EmergentCameraC.h> // Include the C header

int main() {
    // --- Configuration from your YAML ---
    const char* targetSerialNumber = "00710039";
    const char* newIpAddress = "192.168.110.2";
    const char* newSubnetMask = "255.255.255.0";
    const char* newGateway = "0.0.0.0";
    // --- End Configuration ---

    printf("--- eSDK Script: Force IP by Serial Number ---\n");
    printf("Target Serial Number: %s\n", targetSerialNumber);
    printf("Setting Temporary IP Address: %s\n", newIpAddress);
    printf("Setting Subnet Mask: %s\n", newSubnetMask);
    printf("Setting Gateway: %s\n", newGateway);

    // -----------------------------------------------------
    // Call the C API version of EVT_ForceIPEx (4 arguments)
    // -----------------------------------------------------
    printf("\nSending ForceIP command...\n");

    // *** CORRECTED LINE: Removed the 5th NULL argument ***
    EVT_ERROR err = EVT_ForceIPEx(targetSerialNumber, newIpAddress, newSubnetMask, newGateway);

    if (err != EVT_SUCCESS) {
        fprintf(stderr, "[ERROR] Failed to send ForceIP command. Error code: %d\n", err);
        fprintf(stderr, "       (This might indicate a network/socket issue, or invalid input format).\n");
        return 1;
    }

    printf("   ForceIP command sent successfully via broadcast targeting camera SN %s.\n", targetSerialNumber);
    printf("\n   IMPORTANT: EVT_ForceIPEx sets a *temporary* IP address.\n");
    printf("   The camera will revert to its previous IP configuration upon reboot.\n");
    printf("   To set a *permanent* IP, you need to:\n");
    printf("     1. Open the camera (using EVT_CameraOpen, possibly with the temporary IP).\n");
    printf("     2. Use the EVT_IPConfig() function.\n");
    printf("     3. Reboot the camera (e.g., using EVT_ForceReboot() or power cycle).\n");


    printf("\n--- Script Finished ---\n");
    return 0;
}