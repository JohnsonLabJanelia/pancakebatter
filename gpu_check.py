import yaml
import subprocess
import argparse

# Function to load the expected configurations from the YAML file
def load_yaml_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

# Function to get the current GPU configurations from nvidia-smi
def get_current_gpu_configs(verbose=False):
    result = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'],
                            stdout=subprocess.PIPE, text=True)
    lines = result.stdout.strip().split('\n')

    if verbose:
        print("Actual GPU Configurations (from nvidia-smi):")
        print(result.stdout.strip())

    # Parse each line into a dictionary with model and driver_version
    gpus = []
    for line in lines:
        model, driver_version = line.split(', ')
        gpus.append({
            'model': model.strip(),
            'driver_version': driver_version.strip()
        })
    
    return gpus

# Function to get Ubuntu version
def get_ubuntu_version():
    result = subprocess.run(['lsb_release', '-r'], stdout=subprocess.PIPE, text=True)
    return result.stdout.strip().split(":")[1].strip()

# Function to get Kernel version
def get_kernel_version():
    result = subprocess.run(['uname', '-r'], stdout=subprocess.PIPE, text=True)
    return result.stdout.strip()

# Function to get the installed version of Emergent eSDK
def get_esdk_version():
    result = subprocess.run(['apt', 'list', '--installed'], stdout=subprocess.PIPE, text=True)
    for line in result.stdout.strip().split('\n'):
        if 'emergent-esdk-ecapture' in line:
            return line.split()[1].strip()  # Get the version from the output

# Function to compare actual and expected configurations
def compare_configs(actual_configs, expected_configs, verbose=False):
    if len(actual_configs) != len(expected_configs['gpus']):
        print("Mismatch: Number of GPUs does not match expected configuration.")
        return False

    match = True

    # Compare each GPU configuration
    for i, (actual, expected) in enumerate(zip(actual_configs, expected_configs['gpus'])):
        if actual != expected:
            print(f"Mismatch found at GPU {i + 1}: {actual} != {expected}")
            match = False
        elif verbose:
            print(f"GPU {i + 1} matches: {actual}")

    if match:
        print("All GPU configurations match!")
    
    return match

# Function to compare system info (Ubuntu version, kernel, and Emergent eSDK)
def compare_system_info(expected_configs, verbose=False):
    expected_ubuntu = expected_configs['system_info']['ubuntu_version']
    expected_kernel = expected_configs['system_info']['kernel_version']
    expected_esdk = expected_configs['system_info']['esdk_version']

    actual_ubuntu = get_ubuntu_version()
    actual_kernel = get_kernel_version()
    actual_esdk = get_esdk_version()

    if verbose:
        print(f"Expected Ubuntu Version: {expected_ubuntu}, Actual Ubuntu Version: {actual_ubuntu}")
        print(f"Expected Kernel Version: {expected_kernel}, Actual Kernel Version: {actual_kernel}")
        print(f"Expected eSDK Version: {expected_esdk}, Actual eSDK Version: {actual_esdk}")

    if actual_ubuntu != expected_ubuntu:
        print(f"Mismatch: Ubuntu version is {actual_ubuntu}, expected {expected_ubuntu}")
        return False

    if actual_kernel != expected_kernel:
        print(f"Mismatch: Kernel version is {actual_kernel}, expected {expected_kernel}")
        return False

    if actual_esdk != expected_esdk:
        print(f"Mismatch: Emergent eSDK version is {actual_esdk}, expected {expected_esdk}")
        return False

    print("System information matches!")
    return True

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Check GPU and system configurations.")
    parser.add_argument('--verbose', action='store_true', help="Show detailed output")
    args = parser.parse_args()

    # Load expected configurations from the YAML file
    expected_configs = load_yaml_config('gpus_config.yml')

    if args.verbose:
        print("Expected GPU Configurations (from YAML):")
        for gpu in expected_configs['gpus']:
            print(gpu)

    # Get the current GPU configurations from nvidia-smi
    actual_configs = get_current_gpu_configs(verbose=args.verbose)

    # Compare actual configurations with expected configurations
    gpus_match = compare_configs(actual_configs, expected_configs, verbose=args.verbose)

    # Compare system information (Ubuntu version, kernel, and Emergent eSDK)
    system_info_match = compare_system_info(expected_configs, verbose=args.verbose)

    # Final result: Return specific exit codes based on what fails
    if gpus_match and system_info_match:
        print("All configurations match!")
        return 0  # Success
    elif not gpus_match:
        print("GPU mismatch detected!")
        return 1  # GPU mismatch
    elif not system_info_match:
        print("System information mismatch detected!")
        return 2  # System (Ubuntu/kernel/eSDK) mismatch


if __name__ == "__main__":
    main()
