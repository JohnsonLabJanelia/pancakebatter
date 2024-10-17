import yaml
import subprocess
import argparse
import re
import logging
import sys
from datetime import datetime

def setup_logging(log_file):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

def parse_arguments():
    parser = argparse.ArgumentParser(description="Check GPU, system, and camera configurations.")
    parser.add_argument('--verbose', action='store_true', help="Show detailed output")
    parser.add_argument('--log', action='store_true', help="Enable logging to file")
    return parser.parse_args()

# TODO: Refactor this so it is agnostic to the specific NIC model, instead gets NIC info from the YAML config and checks if it is seen in lspci
# def get_emergent_nic_info():
#     """Get information about the Emergent NIC using lspci"""
#     try:
#         result = subprocess.run(['lspci', '-v'], capture_output=True, text=True)
#         lines = result.stdout.split('\n')
#         for i, line in enumerate(lines):
#             if 'Device 1e5e:1002' in line:
#                 for j in range(i, min(i+10, len(lines))):
#                     if 'Kernel driver in use: evt_nic_driver' in lines[j]:
#                         return '\n'.join(lines[i:j+1])
#     except subprocess.CalledProcessError:
#         logging.error("Error running lspci command")
#     return None

# def verify_emergent_nic(expected_config, actual_info):
#     """Verify if the detected Emergent NIC matches the expected configuration"""
#     if actual_info is None:
#         logging.error("Emergent NIC not found")
#         return False
    
#     expected_model = expected_config['emergent_nic'][0]['model']
#     if 'Device 1e5e:1002' in actual_info and 'Kernel driver in use: evt_nic_driver' in actual_info:
#         logging.info(f"Emergent NIC detected: {actual_info}")
#         return True
#     else:
#         logging.error(f"Mismatch: Expected Emergent NIC {expected_model}, but found unexpected configuration")
#         logging.error(actual_info)
#         return False

def verify_nic_ports(expected_configs):
    camera_configs = expected_configs.get('cameras', {})
    network_interfaces = get_network_interfaces()
    mismatches = []

    for mac_address, camera_info in camera_configs.items():
        nic_port = camera_info['nic_port']
        if nic_port not in network_interfaces:
            mismatches.append(f"NIC port {nic_port} specified for camera {mac_address} does not exist on the system.")

    if mismatches:
        logging.error("NIC port mismatches:")
        for mismatch in mismatches:
            logging.error(f"  - {mismatch}")
        return False
    else:
        logging.info("All specified NIC ports exist on the system.")
        return True

def get_network_interfaces():
    """Get all network interfaces and their MAC addresses"""
    interfaces = {}
    current_interface = None
    try:
        result = subprocess.run(['ip', 'link', 'show'], capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line:
                parts = line.split()
                if ':' in parts[0]:
                    current_interface = parts[1].rstrip(':')
                elif parts[0] == 'link/ether':
                    mac = parts[1]
                    interfaces[current_interface] = mac
    except subprocess.CalledProcessError:
        logging.error("Error running ip link show command")
    return interfaces

def check_camera_configurations(expected_configs, verbose=False):
    camera_configs = expected_configs.get('cameras', {})
    mismatches = []

    for mac_address, camera_info in camera_configs.items():
        ip_address = camera_info['ip_address']
        if verbose:
            logging.info(f"Checking camera at {ip_address} with MAC address {mac_address}...")
        try:
            result = subprocess.run(['arping', '-c', '1', ip_address], 
                                    capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                if verbose:
                    logging.info(f"Camera at {ip_address} is reachable.")
            else:
                mismatches.append(f"Camera at {ip_address} is not reachable.")
        except subprocess.TimeoutExpired:
            mismatches.append(f"Timeout while trying to reach camera at {ip_address}.")
        except subprocess.CalledProcessError:
            mismatches.append(f"Error while trying to reach camera at {ip_address}.")

    if mismatches:
        logging.error("Camera configuration mismatches:")
        for mismatch in mismatches:
            logging.error(f"  - {mismatch}")
        return False
    else:
        logging.info("All camera configurations match!")
        return True

def load_yaml_config(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

def get_current_gpu_configs(verbose=False):
    result = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'],
                            stdout=subprocess.PIPE, text=True)
    lines = result.stdout.strip().split('\n')

    if verbose:
        logging.info("Actual GPU Configurations (from nvidia-smi):")
        logging.info(result.stdout.strip())

    gpus = []
    for line in lines:
        model, driver_version = line.split(', ')
        gpus.append({
            'model': model.strip(),
            'driver_version': driver_version.strip()
        })
    
    return gpus

def get_ubuntu_version():
    result = subprocess.run(['lsb_release', '-r'], stdout=subprocess.PIPE, text=True)
    return result.stdout.strip().split(":")[1].strip()

def get_kernel_version():
    result = subprocess.run(['uname', '-r'], stdout=subprocess.PIPE, text=True)
    return result.stdout.strip()

def get_esdk_version():
    result = subprocess.run(['apt', 'list', '--installed'], stdout=subprocess.PIPE, text=True)
    for line in result.stdout.strip().split('\n'):
        if 'emergent-esdk-ecapture' in line:
            return line.split()[1].strip()

def compare_configs(actual_configs, expected_configs, verbose=False):
    if len(actual_configs) != len(expected_configs['gpus']):
        logging.error("Mismatch: Number of GPUs does not match expected configuration.")
        return False

    match = True

    for i, (actual, expected) in enumerate(zip(actual_configs, expected_configs['gpus'])):
        if actual != expected:
            logging.error(f"Mismatch found at GPU {i + 1}: {actual} != {expected}")
            match = False
        elif verbose:
            logging.info(f"GPU {i + 1} matches: {actual}")

    if match:
        logging.info("All GPU configurations match!")
    
    return match

def compare_system_info(expected_configs, verbose=False):
    expected_ubuntu = expected_configs['system_info']['ubuntu_version']
    expected_kernel = expected_configs['system_info']['kernel_version']
    expected_esdk = expected_configs['system_info']['esdk_version']

    actual_ubuntu = get_ubuntu_version()
    actual_kernel = get_kernel_version()
    actual_esdk = get_esdk_version()

    if verbose:
        logging.info(f"Expected Ubuntu Version: {expected_ubuntu}, Actual Ubuntu Version: {actual_ubuntu}")
        logging.info(f"Expected Kernel Version: {expected_kernel}, Actual Kernel Version: {actual_kernel}")
        logging.info(f"Expected eSDK Version: {expected_esdk}, Actual eSDK Version: {actual_esdk}")

    if actual_ubuntu != expected_ubuntu:
        logging.error(f"Mismatch: Ubuntu version is {actual_ubuntu}, expected {expected_ubuntu}")
        return False

    if actual_kernel != expected_kernel:
        logging.error(f"Mismatch: Kernel version is {actual_kernel}, expected {expected_kernel}")
        return False

    if actual_esdk != expected_esdk:
        logging.error(f"Mismatch: Emergent eSDK version is {actual_esdk}, expected {expected_esdk}")
        return False

    logging.info("System information matches!")
    return True

def main():
    args = parse_arguments()
    
    if args.log:
        log_file = f"logs/system_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        setup_logging(log_file)
        logging.info(f"Logging enabled. Log file: {log_file}")
    else:
        logging.basicConfig(level=logging.INFO, format='%(message)s')

    expected_configs = load_yaml_config('system_config.yml')

    if args.verbose:
        logging.info("Expected GPU Configurations (from YAML):")
        for gpu in expected_configs['gpus']:
            logging.info(gpu)

    actual_configs = get_current_gpu_configs(verbose=args.verbose)
    gpus_match = compare_configs(actual_configs, expected_configs, verbose=args.verbose)
    system_info_match = compare_system_info(expected_configs, verbose=args.verbose)
    # actual_nic_info = get_emergent_nic_info()
    # emergent_nic_match = verify_emergent_nic(expected_configs, actual_nic_info)

    logging.info("Detecting network interfaces...")
    network_interfaces = get_network_interfaces()
    logging.info("Detected network interfaces:")
    for interface, mac in network_interfaces.items():
        logging.info(f"  {interface}: {mac}")

    logging.info("Verifying Emergent NIC ports...")
    nic_ports_match = verify_nic_ports(expected_configs)

    cameras_match = check_camera_configurations(expected_configs, verbose=args.verbose)

    # if gpus_match and system_info_match and emergent_nic_match and nic_ports_match and cameras_match:
    if gpus_match and system_info_match and nic_ports_match and cameras_match:
        logging.info("All configurations match!")
        return 0  # Success
    elif not gpus_match:
        logging.error("GPU mismatch detected!")
        return 1
    elif not system_info_match:
        logging.error("System information mismatch detected!")
        return 2
    elif not emergent_nic_match:
        logging.error("NIC mismatch detected!")
        return 3
    elif not nic_ports_match:
        logging.error("NIC port mismatch detected!")
        return 4
    elif not cameras_match:
        logging.error("Camera configuration mismatch detected!")
        return 5
    else:
        logging.error("Unknown error occurred!")
        return 6

if __name__ == "__main__":
    sys.exit(main())