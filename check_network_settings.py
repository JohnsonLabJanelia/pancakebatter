#!/usr/bin/env python3

import subprocess
import logging
import sys
import yaml
from typing import Dict, Any

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

def verify_nic_ports(expected_configs):
    camera_configs = expected_configs.get('cameras', {})
    network_interfaces = get_network_interfaces()
    mismatches = []

    for mac_address, camera_info in camera_configs.items():
        nic_port = camera_info['nic_port']
        if nic_port not in network_interfaces:
            mismatches.append(f"NIC port {nic_port} specified for camera {mac_address} does not exist on the system.")

    if mismatches:
        print("NIC port mismatches:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return False
    else:
        print("All specified NIC ports exist on the system.")
        return True

def check_camera_configurations(expected_configs, verbose=False):
    camera_configs = expected_configs.get('cameras', {})
    mismatches = []

    for mac_address, camera_info in camera_configs.items():
        ip_address = camera_info['ip_address']
        if verbose:
            print(f"Checking camera at {ip_address} with MAC address {mac_address}...")
        try:
            result = subprocess.run(['arping', '-c', '1', ip_address], 
                                  capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                if verbose:
                    print(f"Camera at {ip_address} is reachable.")
            else:
                mismatches.append(f"Camera at {ip_address} is not reachable.")
        except subprocess.TimeoutExpired:
            mismatches.append(f"Timeout while trying to reach camera at {ip_address}.")
        except subprocess.CalledProcessError:
            mismatches.append(f"Error while trying to reach camera at {ip_address}.")

    if mismatches:
        print("Camera configuration mismatches:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return False
    else:
        print("All camera configurations match!")
        return True

def main():
    if len(sys.argv) < 2:
        print("Usage: check_network.py <config_file> [--verbose]", file=sys.stderr)
        return 1

    config_file = sys.argv[1]
    verbose = "--verbose" in sys.argv

    try:
        with open(config_file) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config file: {e}", file=sys.stderr)
        return 1

    interfaces = get_network_interfaces()
    if verbose:
        print("\nDetected network interfaces:")
        for interface, mac in interfaces.items():
            print(f"  {interface}: {mac}")

    print("\nVerifying NIC ports...")
    nic_match = verify_nic_ports(config)
    
    print("\nChecking camera configurations...")
    camera_match = check_camera_configurations(config, verbose)

    return 0 if (nic_match and camera_match) else 1

if __name__ == "__main__":
    sys.exit(main())