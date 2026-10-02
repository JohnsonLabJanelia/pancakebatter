#!/bin/bash

# Script for setting up network configurations using NetworkManager
# Incorporates dynamic config file loading, dependency checks, nmcli usage, and idempotency checks.
# Should be run AFTER rebooting with udev rules applied by network_alias_assignment.sh

# ANSI color codes
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
NC="\033[0m"  # No Color

# --- Configuration ---
# Get hostname (use -s for short name, adjust if your naming needs FQDN)
source "$(dirname "${BASH_SOURCE[0]}")/lib/host_config.sh"
HOSTNAME="$HOST_NAME"
CONFIG_FILE="$HOST_CONFIG_FILE"
# User who invoked sudo, for running yq safely
INVOKING_USER="$SUDO_USER"

# --- Helper Functions ---
# Function to check for required commands
check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"
    local missing_deps=0
    # Include ethtool for verification, ip for fallback checks
    local deps=("ip" "ethtool" "yq" "nmcli")
    for cmd in "${deps[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            echo -e "${RED}Error: Required command '$cmd' not found.${NC}"
            missing_deps=1
        fi
    done
    if [[ "$missing_deps" -eq 0 ]] && ! printf '{}\n' | sudo -u "$INVOKING_USER" yq e '.test' - >/dev/null 2>&1; then
        echo -e "${RED}Error: Installed yq does not support 'yq e' syntax. Run install_yq.sh.${NC}"
        missing_deps=1
    fi
    if [[ "$missing_deps" -eq 1 ]]; then
        echo -e "${RED}Please install missing dependencies and try again.${NC}"
        exit 1
    fi
    echo -e "${GREEN}All dependencies found.${NC}"
}

# Function to safely parse YAML using yq as the invoking user
safe_yq_get() {
    local query=$1
    local file=$2
    sudo -u "$INVOKING_USER" yq e "$query" "$file" 2>/dev/null
    # Check yq exit status. 0=success, 4=null/empty, other=error
    local exit_status=$?
    if [[ $exit_status -ne 0 && $exit_status -ne 4 ]]; then
         echo "YAML_PARSE_ERROR" # Special string to indicate failure
         return 1
    fi
    # Return the actual output which might be empty/null if exit_status was 4
    return $exit_status
}

# Function to find NetworkManager connection UUID for a given device name
get_nmcli_connection_uuid() {
    local interface=$1
    local uuid
    # Use -t for terse output, -f fields, grep for the device, cut the UUID
    # Prioritize active connections
    uuid=$(nmcli -t -f UUID,DEVICE connection show --active | grep ":${interface}$" | cut -d':' -f1)
    if [[ -z "$uuid" ]]; then
         # Try inactive connections as well
         uuid=$(nmcli -t -f UUID,DEVICE connection show | grep ":${interface}$" | cut -d':' -f1)
    fi

    if [[ -n "$uuid" ]]; then
        echo "$uuid"
        return 0
    else
        # If no connection uses this DEVICE name, maybe a connection exists matching the interface NAME?
        echo -e "${YELLOW}  Warning: Could not find NM connection with DEVICE='$interface'. Trying by NAME='$interface'...${NC}" >&2
        uuid=$(nmcli -t -f UUID,NAME connection show | grep ":${interface}$" | cut -d':' -f1)
        if [[ -n "$uuid" ]]; then
             echo -e "${YELLOW}  Found connection UUID by NAME. Ensure this connection is correctly bound to the device '$interface'.${NC}" >&2
             echo "$uuid"
             return 0
        else
             echo -e "${RED}  Error: Failed to find any NetworkManager connection associated with interface '$interface'. Cannot configure via nmcli.${NC}" >&2
             echo -e "${RED}  You may need to manually create a connection for this interface first (e.g., 'nmcli connection add type ethernet ifname $interface con-name $interface')${NC}" >&2
             return 1
        fi
    fi
}

# --- Main Script ---

# Check if running as root
if [[ "$EUID" -ne 0 ]]; then
    echo -e "${RED}Please run as root using sudo${NC}"
    exit 1
fi

# Ensure SUDO_USER is set
if [[ -z "$INVOKING_USER" ]]; then
     echo -e "${RED}Error: Could not determine the original user. Please run using sudo.${NC}"
     exit 1
fi

# Check dependencies first
check_dependencies

# Check if config file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo -e "${RED}Configuration file '$CONFIG_FILE' not found for hostname '$HOSTNAME'.${NC}"
    exit 1
fi
echo -e "${GREEN}Using configuration file: $CONFIG_FILE${NC}"

# Parse the number of NICs from the config file
nics_length_raw=$(safe_yq_get '.nics | length' "$CONFIG_FILE")
if [[ $? -ne 0 || ! "$nics_length_raw" =~ ^[0-9]+$ ]]; then
     echo -e "${RED}Error: Could not parse number of NICs from $CONFIG_FILE${NC}"
     exit 1
fi
nics_length=$nics_length_raw

echo -e "${YELLOW}Configuring network interfaces via NetworkManager...${NC}"
needs_reactivation=() # Array to hold UUIDs that might need reactivation
config_errors=0

# Loop through NICs defined in the config file
for ((i=0; i<$nics_length; i++)); do
    # Get the primary interface name (the key under nics[i]) - this should be the NAME set by udev
    nic_name=$(safe_yq_get ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    if [[ $? -ne 0 || "$nic_name" == "YAML_PARSE_ERROR" || -z "$nic_name" || "$nic_name" == "null" ]]; then
         echo -e "${RED}Error parsing NIC name at index $i in $CONFIG_FILE${NC}"
         ((config_errors++))
         continue # Skip to next NIC
    fi

    managed=$(safe_yq_get ".nics[$i].$nic_name.managed" "$CONFIG_FILE")
    if [[ "$managed" == "YAML_PARSE_ERROR" ]]; then
         echo -e "${RED}Error parsing managed flag for $nic_name from config.${NC}"
         ((config_errors++))
         continue
    fi
    if [[ "$managed" == "false" ]]; then
         echo -e "\n${YELLOW}Skipping configured interface: $nic_name (managed=false)${NC}"
         continue
    fi

    echo -e "\n${BLUE}Processing configured interface: $nic_name${NC}"

    # Check if the physical interface actually exists (using the potentially renamed name)
    if ! ip link show "$nic_name" &> /dev/null; then
        echo -e "${RED}  Error: Interface '$nic_name' not found on system. Was udev rule applied and system rebooted? Skipping.${NC}"
        ((config_errors++))
        continue
    fi

    # Get the connection UUID for this interface
    connection_uuid=$(get_nmcli_connection_uuid "$nic_name")
    if [[ -z "$connection_uuid" ]]; then
        # Error message printed by function
        ((config_errors++))
        continue # Skip to next NIC
    fi
    echo -e "  Found NM connection UUID: $connection_uuid"

    # Get desired settings from YAML, checking for parse errors
    ip_address=$(safe_yq_get ".nics[$i].$nic_name.ip_address" "$CONFIG_FILE")
    [[ $? -eq 0 ]] || ip_address="YAML_PARSE_ERROR"
    mtu=$(safe_yq_get ".nics[$i].$nic_name.mtu" "$CONFIG_FILE")
    [[ $? -eq 0 ]] || mtu="YAML_PARSE_ERROR"
    speed=$(safe_yq_get ".nics[$i].$nic_name.link_settings.speed" "$CONFIG_FILE")
    [[ $? -eq 0 ]] || speed="YAML_PARSE_ERROR"
    autoneg_str=$(safe_yq_get ".nics[$i].$nic_name.link_settings.autoneg" "$CONFIG_FILE")
    [[ $? -eq 0 ]] || autoneg_str="YAML_PARSE_ERROR"

    # --- Prepare nmcli modifications ---
    mods=()
    config_changed=0
    current_nic_errors=0

    # 1. IP Address Configuration
    if [[ "$ip_address" != "YAML_PARSE_ERROR" && -n "$ip_address" && "$ip_address" != "null" ]]; then
        # Basic validation of IP/CIDR format - enhance regex if needed
        if [[ ! "$ip_address" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/[0-9]{1,2}$ ]]; then
             echo -e "${RED}  Invalid IP address format in config: '$ip_address'${NC}"
             ((current_nic_errors++))
        else
            current_ip_cidr=$(nmcli -g IP4.ADDRESS connection show "$connection_uuid")
            current_method=$(nmcli -g IPV4.METHOD connection show "$connection_uuid")

            if [[ "$current_method" != "manual" || "$current_ip_cidr" != "$ip_address" ]]; then
                echo -e "  Updating IP address to $ip_address (manual method)"
                mods+=("ipv4.method manual" "ipv4.addresses $ip_address")
                # Also ensure gateway is cleared unless specified in config (not currently read)
                # mods+=("ipv4.gateway ''")
                config_changed=1
            else
                echo -e "  IP address ($current_ip_cidr) and method ($current_method) already correct."
            fi
        fi
    elif [[ "$ip_address" == "YAML_PARSE_ERROR" ]]; then
         echo -e "${RED}  Error parsing IP address for $nic_name from config.${NC}"
         ((current_nic_errors++))
    else
         echo -e "  No IP address specified for $nic_name in config. Ensuring method is not manual."
         current_method=$(nmcli -g IPV4.METHOD connection show "$connection_uuid")
         if [[ "$current_method" == "manual" ]]; then
              echo -e "  Setting IPv4 method to disabled (use 'auto' for DHCP)."
              mods+=("ipv4.method disabled" "ipv4.addresses ''") # Or auto? Depends on desired default
              config_changed=1
         fi
    fi

    # 2. MTU Configuration
    if [[ "$mtu" != "YAML_PARSE_ERROR" && -n "$mtu" && "$mtu" != "null" ]]; then
        if [[ ! "$mtu" =~ ^[0-9]+$ || "$mtu" -lt 68 ]]; then # Basic MTU check
             echo -e "${RED}  Invalid MTU value in config: '$mtu'${NC}"
             ((current_nic_errors++))
        else
            current_mtu=$(nmcli -g 802-3-ETHERNET.MTU connection show "$connection_uuid")
            # NM uses '0' for auto/default MTU
            if [[ "$current_mtu" != "$mtu" ]]; then
                # If current is '0', check effective MTU before changing unnecessarily
                change_needed=1
                if [[ "$current_mtu" == "0" ]]; then
                     current_mtu_effective=$(ip -o link show "$nic_name" | grep -o 'mtu [0-9]*' | cut -d' ' -f2 || echo "unknown")
                     echo -e "  Current effective MTU for $nic_name is $current_mtu_effective (NM setting is auto)"
                     if [[ "$current_mtu_effective" == "$mtu" ]]; then
                          echo -e "  Effective MTU already matches desired $mtu. No change needed."
                          change_needed=0
                     fi
                fi

                if [[ "$change_needed" -eq 1 ]]; then
                     echo -e "  Updating MTU to $mtu"
                     mods+=("802-3-ethernet.mtu $mtu")
                     config_changed=1
                fi
            else
                echo -e "  MTU ($current_mtu) already correct."
            fi
        fi
    elif [[ "$mtu" == "YAML_PARSE_ERROR" ]]; then
         echo -e "${RED}  Error parsing MTU for $nic_name from config.${NC}"
         ((current_nic_errors++))
    else
         echo -e "  No MTU specified for $nic_name in config. Ensuring MTU is auto."
         current_mtu=$(nmcli -g 802-3-ETHERNET.MTU connection show "$connection_uuid")
         if [[ "$current_mtu" != "0" ]]; then
              echo -e "  Setting MTU to auto (0)."
              mods+=("802-3-ethernet.mtu 0")
              config_changed=1
         fi
    fi

    # 3. Speed and Autonegotiation
    if [[ "$autoneg_str" != "YAML_PARSE_ERROR" && -n "$autoneg_str" && "$autoneg_str" != "null" ]]; then
        current_autoneg=$(nmcli -g 802-3-ETHERNET.AUTO-NEGOTIATE connection show "$connection_uuid") # yes/no
        current_speed=$(nmcli -g 802-3-ETHERNET.SPEED connection show "$connection_uuid") # Speed in Mbps, 0 for auto

        desired_autoneg="yes" # Default to yes unless explicitly "false"
        if [[ "$autoneg_str" == "false" ]]; then
            desired_autoneg="no"
        fi

        if [[ "$current_autoneg" != "$desired_autoneg" ]]; then
            echo -e "  Updating autonegotiation to $desired_autoneg"
            mods+=("802-3-ethernet.auto-negotiate $desired_autoneg")
            config_changed=1
        else
            echo -e "  Autonegotiation ($current_autoneg) already correct."
        fi

        # Only check/set speed if autoneg is OFF
        if [[ "$desired_autoneg" == "no" ]]; then
            if [[ "$speed" != "YAML_PARSE_ERROR" && -n "$speed" && "$speed" != "null" && "$speed" =~ ^[0-9]+$ ]]; then
                 if [[ "$current_speed" != "$speed" ]]; then
                      echo -e "  Updating speed to ${speed}Mbps (with autoneg off)"
                      mods+=("802-3-ethernet.speed $speed")
                      # NM often requires duplex when speed is set manually, default to full
                      mods+=("802-3-ethernet.duplex full")
                      config_changed=1
                 else
                      echo -e "  Speed ($current_speed Mbps) already correct (with autoneg off)."
                 fi
            elif [[ "$speed" == "YAML_PARSE_ERROR" ]]; then
                 echo -e "${RED}  Error parsing Speed for $nic_name from config.${NC}"
                 ((current_nic_errors++))
            else
                 echo -e "${YELLOW}  Warning: Autoneg is off but no valid speed specified in config for $nic_name. Speed setting may be inconsistent.${NC}"
                 # Maybe force speed to 0 (auto) if autoneg is off but speed isn't set? Or leave it? Leaving it for now.
            fi
        elif [[ "$current_speed" != "0" ]]; then
             # If autoneg is ON, ensure speed/duplex are reset to auto (0 / empty) in NM
             echo -e "  Resetting speed/duplex to auto (due to autoneg on)"
             mods+=("802-3-ethernet.speed 0" "802-3-ethernet.duplex ''")
             config_changed=1
        fi
    elif [[ "$autoneg_str" == "YAML_PARSE_ERROR" ]]; then
        echo -e "${RED}  Error parsing Autoneg setting for $nic_name from config.${NC}"
        ((current_nic_errors++))
    else
        echo -e "  No Autoneg setting specified for $nic_name in config. Assuming defaults."
        # Optionally ensure settings are auto if not specified?
    fi

    # --- Apply modifications if needed and no errors for this NIC ---
    if [[ "$config_changed" -eq 1 && "$current_nic_errors" -eq 0 ]]; then
        echo -e "  Applying changes to connection $connection_uuid..."
        # Build the command safely using an array
        nmcli_cmd_array=(nmcli connection modify "$connection_uuid")
        for mod in "${mods[@]}"; do
            nmcli_cmd_array+=($mod) # Add setting and value as separate elements
        done

        # Execute the command
        if "${nmcli_cmd_array[@]}"; then
             echo -e "${GREEN}  Configuration applied successfully.${NC}"
             needs_reactivation+=("$connection_uuid") # Mark for potential reactivation
        else
             echo -e "${RED}  Error applying configuration changes for $connection_uuid.${NC}"
             ((config_errors++))
        fi
    elif [[ "$current_nic_errors" -gt 0 ]]; then
         echo -e "${RED}  Skipping application of changes for $nic_name due to validation/parsing errors.${NC}"
         ((config_errors++))
    else
        echo -e "  No changes needed for connection $connection_uuid."
    fi

done # End loop through NICs

# --- Reactivate connections if needed ---
if [[ ${#needs_reactivation[@]} -gt 0 && "$config_errors" -eq 0 ]]; then
     echo -e "\n${YELLOW}Reactivating modified connections to ensure settings take effect...${NC}"
     # Use a temporary associative array to store unique UUIDs
     declare -A unique_uuids
     for uuid in "${needs_reactivation[@]}"; do
         unique_uuids["$uuid"]=1
     done

     for uuid in "${!unique_uuids[@]}"; do
         echo -e "  Attempting to reactivate connection $uuid..."
         # Bring down first, then up, to ensure settings apply
         # Redirect stderr for 'down' as it might fail if already down, which is okay
         if nmcli connection down "$uuid" &> /dev/null || [[ $? -eq 0 ]]; then
             sleep 1 # Give it a moment
             if nmcli connection up "$uuid"; then
                 echo -e "${GREEN}  Connection $uuid reactivated successfully.${NC}"
             else
                 echo -e "${RED}  Error reactivating connection $uuid. Check 'journalctl -u NetworkManager' or 'nmcli connection show $uuid'.${NC}"
                 ((config_errors++))
             fi
         else
              # If 'down' failed critically (not just 'already down'), maybe try 'up' anyway?
              echo -e "${YELLOW}  Connection $uuid 'down' command failed unexpectedly. Attempting 'up' anyway...${NC}"
              if nmcli connection up "$uuid"; then
                 echo -e "${GREEN}  Connection $uuid activated successfully.${NC}"
              else
                 echo -e "${RED}  Error activating connection $uuid after 'down' failed. Check system logs.${NC}"
                 ((config_errors++))
              fi
         fi
     done
elif [[ "$config_errors" -gt 0 ]]; then
     echo -e "\n${YELLOW}Skipping connection reactivation due to configuration errors.${NC}"
fi

# --- Final Verification ---
echo -e "\n${YELLOW}Verifying final interface configurations:${NC}"
# Re-parse length just in case
nics_length_verify_raw=$(safe_yq_get '.nics | length' "$CONFIG_FILE")
if [[ $? -ne 0 || ! "$nics_length_verify_raw" =~ ^[0-9]+$ ]]; then exit 1; fi
nics_length_verify=$nics_length_verify_raw

for ((i=0; i<$nics_length_verify; i++)); do
    nic_name=$(safe_yq_get ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    if [[ $? -ne 0 || "$nic_name" == "YAML_PARSE_ERROR" || -z "$nic_name" || "$nic_name" == "null" ]]; then continue; fi

    managed=$(safe_yq_get ".nics[$i].$nic_name.managed" "$CONFIG_FILE")
    if [[ "$managed" == "false" ]]; then
        echo -e "\n${YELLOW}Skipping verification for $nic_name (managed=false)${NC}"
        continue
    fi

    echo -e "\n${BLUE}Verifying Interface (from config): $nic_name${NC}"
    if ip link show "$nic_name" &> /dev/null; then
        connection_uuid=$(get_nmcli_connection_uuid "$nic_name") # Get UUID again for verification context
        if [[ -n "$connection_uuid" ]]; then
             echo "  NM Connection UUID: $connection_uuid"
             # Use -p for pretty print to make it easier to read properties
             nmcli -p connection show "$connection_uuid" | grep -E "ipv4.method|ipv4.addresses|ipv4.gateway|802-3-ethernet.mtu|802-3-ethernet.auto-negotiate|802-3-ethernet.speed"
        else
             echo "  Could not find associated NM connection for verification."
        fi
        # Also show low-level tool output for cross-check
        echo "  IP Addr Info:"
        ip -o -4 addr show dev "$nic_name" | awk '{print "    "$0}' # Indent output
        echo "  Ethtool Info:"
        # Indent ethtool output
        ethtool "$nic_name" | grep -E "Speed:|Duplex:|Auto-negotiation:|Link detected:" | awk '{print "    "$0}'
        echo "  MTU Info:"
        ip -o link show "$nic_name" | grep -o 'mtu [0-9]*' | awk '{print "    "$0}'

    else
        echo -e "${YELLOW}  Interface $nic_name not found during verification.${NC}"
    fi
done

if [[ "$config_errors" -gt 0 ]]; then
     echo -e "\n${RED}Network interface configuration script finished with $config_errors error(s). Please review logs.${NC}"
     exit 1
else
     echo -e "\n${GREEN}Network interface configuration script finished successfully.${NC}"
     exit 0
fi
