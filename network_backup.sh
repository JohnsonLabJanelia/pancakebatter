#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo bash $0)"
    exit 1
fi

# Get the invoking user (the user who ran sudo)
REAL_USER=$(logname || echo $SUDO_USER)
if [ -z "$REAL_USER" ]; then
    echo "Could not determine the real user, using /tmp for output"
    FINAL_DIR="/tmp"
else
    FINAL_DIR="/home/$REAL_USER"
fi

# Create backup directory with timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/root/network-config-backup-$TIMESTAMP"
mkdir -p "$BACKUP_DIR"

# Backup all potential network configuration files
echo "Creating network configuration backup in $BACKUP_DIR..."

# udev rules
if [ -d "/etc/udev/rules.d" ]; then
    mkdir -p "$BACKUP_DIR/udev/rules.d"
    cp /etc/udev/rules.d/*network*.rules "$BACKUP_DIR/udev/rules.d/" 2>/dev/null
    cp /etc/udev/rules.d/*net*.rules "$BACKUP_DIR/udev/rules.d/" 2>/dev/null
fi

# Netplan
if [ -d "/etc/netplan" ]; then
    mkdir -p "$BACKUP_DIR/netplan"
    cp /etc/netplan/*.yaml "$BACKUP_DIR/netplan/" 2>/dev/null
fi

# Classic networking
if [ -d "/etc/network" ]; then
    mkdir -p "$BACKUP_DIR/network"
    cp /etc/network/interfaces "$BACKUP_DIR/network/" 2>/dev/null
    cp -r /etc/network/interfaces.d "$BACKUP_DIR/network/" 2>/dev/null
fi

# systemd-networkd
if [ -d "/etc/systemd/network" ]; then
    mkdir -p "$BACKUP_DIR/systemd"
    cp -r /etc/systemd/network "$BACKUP_DIR/systemd/" 2>/dev/null
fi

# NetworkManager
if [ -d "/etc/NetworkManager/system-connections" ]; then
    mkdir -p "$BACKUP_DIR/NetworkManager"
    cp -r /etc/NetworkManager/system-connections "$BACKUP_DIR/NetworkManager/" 2>/dev/null
fi

# Create a tar archive directly in the user's home directory
tar -czf "$FINAL_DIR/network-config-backup-$TIMESTAMP.tar.gz" -C /root "network-config-backup-$TIMESTAMP"

# Clean up the temporary directory in /root
rm -rf "$BACKUP_DIR"

# Set proper ownership of the archive
chown $REAL_USER:$REAL_USER "$FINAL_DIR/network-config-backup-$TIMESTAMP.tar.gz"

echo "Backup completed and cleaned up."
echo "Archive created: $FINAL_DIR/network-config-backup-$TIMESTAMP.tar.gz"