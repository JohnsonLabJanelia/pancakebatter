# #!/usr/bin/env python3

# import yaml
# import subprocess
# import sys
# import re
# from typing import Dict, Any, Tuple
# from pathlib import Path

# # ANSI color codes
# RED = "\033[0;31m"
# GREEN = "\033[0;32m"
# YELLOW = "\033[0;33m"
# BLUE = "\033[0;34m"
# NC = "\033[0m"

# def print_colored(color: str, message: str) -> None:
#     """Print a message in color"""
#     print(f"{color}{message}{NC}")

# def get_interface_info(interface: str) -> Dict[str, Any]:
#     """Get actual interface information from system"""
#     try:
#         # Check if interface exists first
#         ip_link = subprocess.run(['ip', 'link', 'show', interface], 
#                                capture_output=True, text=True)
#         if ip_link.returncode != 0:
#             print_colored(RED, f"Interface {interface} not found")
#             return None
            
#         ip_addr = subprocess.run(['ip', 'addr', 'show', interface], 
#                                capture_output=True, text=True)
        
#         info = {}
        
#         # Parse MAC address
#         mac_match = re.search(r'link/ether ([0-9a-f:]{17})', ip_link.stdout)
#         if not mac_match:
#             print_colored(YELLOW, f"Could not find MAC address for {interface}")
#             return None
#         info['mac_address'] = mac_match.group(1)
        
#         # Parse MTU
#         mtu_match = re.search(r'mtu (\d+)', ip_link.stdout)
#         if not mtu_match:
#             print_colored(YELLOW, f"Could not find MTU for {interface}")
#             return None
#         info['mtu'] = int(mtu_match.group(1))
        
#         # Parse IP address
#         ip_match = re.search(r'inet ([\d.]+)/(\d+)', ip_addr.stdout)
#         if not ip_match:
#             print_colored(YELLOW, f"Could not find IP address for {interface}")
#             return None
#         info['ip_address'] = f"{ip_match.group(1)}/{ip_match.group(2)}"
        
#         # Parse alternative name
#         alt_match = re.search(r'altname (\S+)', ip_link.stdout)
#         if alt_match:
#             info['altname'] = alt_match.group(1)
        
#         # Check interface state
#         info['state'] = 'UP' if 'state UP' in ip_link.stdout else 'DOWN'
        
#         # Parse queue length
#         qlen_match = re.search(r'qlen (\d+)', ip_link.stdout)
#         if qlen_match:
#             info['qlen'] = int(qlen_match.group(1))
            
#         return info
#     except subprocess.CalledProcessError as e:
#         print_colored(RED, f"Error getting information for interface {interface}: {str(e)}")
#         return None
#     except Exception as e:
#         print_colored(RED, f"Unexpected error for interface {interface}: {str(e)}")
#         return None

# def check_interfaces(config_file: str) -> Tuple[int, str]:
#     """Check network interfaces against configuration"""
#     try:
#         with open(config_file, 'r') as f:
#             config = yaml.safe_load(f)
#     except Exception as e:
#         return 1, f"Error loading config file: {e}"

#     errors = 0
#     report_lines = []
    
#     if 'nics' not in config:
#         print_colored(RED, "No 'nics' section found in configuration file")
#         return 1, "Missing network configuration"

#     for nic in config['nics']:
#         # Get the interface name (first key in the dictionary)
#         try:
#             interface_name = list(nic.keys())[0]
#             expected = nic[interface_name]
#         except (IndexError, KeyError) as e:
#             print_colored(RED, f"Error parsing NIC configuration: {str(e)}")
#             errors += 1
#             continue
            
#         report_lines.append(f"\nChecking interface: {interface_name}")
        
#         actual = get_interface_info(interface_name)
#         if not actual:
#             errors += 1
#             continue
        
#         # Check MAC address
#         if 'mac_address' in expected:
#             if actual['mac_address'].lower() == expected['mac_address'].lower():
#                 report_lines.append(f"{GREEN}✓ MAC address matches: {actual['mac_address']}{NC}")
#             else:
#                 report_lines.append(
#                     f"{RED}✗ MAC address mismatch: {actual['mac_address']} "
#                     f"(expected {expected['mac_address']}){NC}"
#                 )
#                 errors += 1
                
#         # Check MTU
#         if 'mtu' in expected:
#             if actual['mtu'] == expected['mtu']:
#                 report_lines.append(f"{GREEN}✓ MTU matches: {actual['mtu']}{NC}")
#             else:
#                 report_lines.append(
#                     f"{RED}✗ MTU mismatch: {actual['mtu']} "
#                     f"(expected {expected['mtu']}){NC}"
#                 )
#                 errors += 1
                
#         # Check IP address
#         if 'ip_address' in expected:
#             if actual['ip_address'] == expected['ip_address']:
#                 report_lines.append(f"{GREEN}✓ IP address matches: {actual['ip_address']}{NC}")
#             else:
#                 report_lines.append(
#                     f"{RED}✗ IP address mismatch: {actual['ip_address']} "
#                     f"(expected {expected['ip_address']}){NC}"
#                 )
#                 errors += 1

#     return errors, "\n".join(report_lines)

# def main():
#     if len(sys.argv) != 2:
#         print(f"Usage: {sys.argv[0]} <config_file>")
#         return 1
        
#     config_file = sys.argv[1]
#     if not Path(config_file).exists():
#         print_colored(RED, f"Config file not found: {config_file}")
#         return 1

#     errors, report = check_interfaces(config_file)
#     print(report)
#     return errors

# if __name__ == "__main__":
#     sys.exit(main())

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