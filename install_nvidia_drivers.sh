#!/bin/bash

# Name of the NVIDIA driver file
NVIDIA_DRIVER="NVIDIA-Linux-x86_64-535.183.06.run"

# Function to check if we're in a graphical environment
check_graphical_env() {
    if [ -n "$DISPLAY" ]; then
        echo "Error: This script should be run from a text-only console (TTY)."
        echo "Please switch to a TTY (Ctrl+Alt+F3) and run the script again."
        exit 1
    fi
}

# Function to check if Nouveau is blacklisted
is_nouveau_blacklisted() {
    if grep -q "blacklist nouveau" /etc/modprobe.d/blacklist-nouveau.conf 2>/dev/null; then
        return 0  # True, Nouveau is blacklisted
    else
        return 1  # False, Nouveau is not blacklisted
    fi
}

# Function to disable Nouveau
disable_nouveau() {
    echo "Disabling Nouveau..."
    sudo bash -c "echo 'blacklist nouveau' > /etc/modprobe.d/blacklist-nouveau.conf"
    sudo bash -c "echo 'options nouveau modeset=0' >> /etc/modprobe.d/blacklist-nouveau.conf"
    sudo update-initramfs -u
    echo "Nouveau has been blacklisted. A reboot is required before continuing."
    read -p "Do you want to reboot now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]
    then
        sudo reboot
    else
        echo "Please reboot manually before continuing with the NVIDIA driver installation."
        exit 0
    fi
}

# Function to stop the display manager
stop_display_manager() {
    if systemctl is-active --quiet gdm; then
        sudo systemctl stop gdm
    elif systemctl is-active --quiet lightdm; then
        sudo systemctl stop lightdm
    else
        echo "Warning: Could not detect active display manager. Proceeding anyway."
    fi
}

# Main installation function
install_nvidia_driver() {
    if [ ! -f "$NVIDIA_DRIVER" ]; then
        echo "Error: NVIDIA driver file not found: $NVIDIA_DRIVER"
        exit 1
    fi

    chmod +x "$NVIDIA_DRIVER"

    # Run the installer with automatic options
    sudo ./"$NVIDIA_DRIVER" -s -a --no-questions

    if [ $? -eq 0 ]; then
        echo "NVIDIA driver installation completed successfully."
        echo "Please reboot your system to complete the installation."
    else
        echo "Error: NVIDIA driver installation failed."
        exit 1
    fi
}

# Main execution
check_graphical_env

# Check if Nouveau is already blacklisted
if is_nouveau_blacklisted; then
    echo "Nouveau is already blacklisted."
else
    echo "Nouveau is not blacklisted."
    if lsmod | grep -q nouveau; then
        echo "Nouveau is currently loaded. It needs to be disabled."
        disable_nouveau
        # Script will exit here if Nouveau was disabled, requiring a reboot
    else
        echo "Nouveau is not currently loaded, but it's not blacklisted. Blacklisting now."
        disable_nouveau
        # Script will exit here after blacklisting, requiring a reboot
    fi
fi

stop_display_manager
install_nvidia_driver

echo "Installation process complete. It's recommended to reboot now."
read -p "Do you want to reboot now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    sudo reboot
fi
