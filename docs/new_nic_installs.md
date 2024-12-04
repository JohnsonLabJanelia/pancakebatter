# Procedure for Updating udev Rules and netplan for New NICs

If you install a new NIC or change the location of a NIC on the motherboard
of a pancake, you should be sure that you update the `system_config.yml` file with the new PCIe lane information. This can be found using:

`lspci -vv | grep Mellanox`

It will tell you the PCIe bus of each port found on your card and the `system_config.yml` can be updated accordingly.

## Update udev Rules

Next, you will want to update your udev rules file. udev rules are configuration files that dynamically manage device attributes, names, and actions. The file can be found at:

`/etc/udev/rules.d/10-network-aliases.rules`

It can look something like this as seen in `pancake0`:

```
jeremy@pancake0:~/pancakebatter$ sudo cat /etc/udev/rules.d/10-network-aliases.rules 
# Custom udev rules for network alias assignment
# Mellanox NVIDIA ConnectX-7 MT2910 Card 1
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9e", NAME="mlnx1_p1_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9e", SYMLINK+="eth0"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9f", NAME="mlnx1_p2_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9f", SYMLINK+="eth1"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:a0", NAME="mlnx1_p3_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:a0", SYMLINK+="eth2"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:a1", NAME="mlnx1_p4_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:a1", SYMLINK+="eth3"
# Mellanox NVIDIA ConnectX-7 MT2910 Card 2
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f0", NAME="mlnx2_p1_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f0", SYMLINK+="eth4"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f1", NAME="mlnx2_p2_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f1", SYMLINK+="eth5"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f2", NAME="mlnx2_p3_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f2", SYMLINK+="eth6"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f3", NAME="mlnx2_p4_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="c4:70:bd:d4:b5:f3", SYMLINK+="eth7"
```

Where each line means:
- `SUBSYSTEM=="net"`
Rule applied only to devices in the `net` subsystem which includes network interfaces.
- `ACTION=="add"`
This specifies that the rule is triggered when the device is added to the system. Specifically, it is executed when the interface is intialized when the interface comes up.
- `ATTR{address}=="a0:88:c2:69:11:9e"`
This filters the rule to only apply to the device with this specific MAC Address
- `NAME="mlnx1_p1_25g"`
This sets the name of the network interface so its more user friendly.
- `SYMLINK+="eth0"`
This creates an alias for the interface as well as a symlink to /dev/eth0 that points to the device node.

Once you've created your udev rules, you need to have the system apply them on the next reboot. You do this through the following commands:
```
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## Update netplan

Now, you need to update your netplan file. Netplan files are YAML-based configuration files in Linux used to define and manage network settings, such as IP addresses and routes, in a declarative and unified way. The file can be found at:

`/etc/netplan/01-network-config.yaml`

It can look something like this as in `pancake0`:

```
jeremy@pancake0:~/pancakebatter$ sudo cat /etc/netplan/01-network-config.yaml 
network:
  version: 2
  renderer: networkd
  ethernets:
    eth_internet2:
      match:
        name: eth_internet2
      dhcp4: true
    mlnx1_p1_25g:
      match:
        macaddress: "a0:88:c2:69:11:9e"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.110.1/24]
      mtu: 9000
    mlnx1_p2_25g:
      match:
        macaddress: "a0:88:c2:69:11:9f"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.120.1/24]
      mtu: 9000
    mlnx1_p3_25g:
      match:
        macaddress: "a0:88:c2:69:11:a0"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.130.1/24]
    mlnx1_p4_25g:
      match:
        macaddress: "a0:88:c2:69:11:a1"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.140.1/24]
    mlnx2_p1_25g:
      match:
        macaddress: "c4:70:bd:d4:b5:f0"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.150.1/24]
      mtu: 9000
    mlnx2_p2_25g:
      match:
        macaddress: "c4:70:bd:d4:b5:f1"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.160.1/24]
      mtu: 9000
    mlnx2_p3_25g:
      match:
        macaddress: "c4:70:bd:d4:b5:f2"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.170.1/24]
      mtu: 9000
    mlnx2_p4_25g:
      match:
        macaddress: "c4:70:bd:d4:b5:f3"
      dhcp4: false
      dhcp6: false
      addresses: [192.168.180.1/24]
      mtu: 9000
```

Where each line means:
- `network:`
A top level key specifying that a file contains network configuration settings
- `version: 2`
The version of which Netplan schema is used.
- `renderer: networkd`
Specifies the backend renderer that applies your configuration. Pancakes rely on `networkd`.
- `ethernets:`
Defines the beginning of where Ethernet interface configurations begin

### Dynamic IP Configuration for Ethernet
Ethernet Port 2 (`eth_internet2`) is used for access to the internet and greater campus network.
- `match`/`name:`
Specifies how to identify the physical linterface you want to associate with the configuration. In this case the interface `eth_internet2` is configured.
- `dhcp4: true`
This enables IPv4 DHCP (Dynamic Host Configuration Protocol) so it receives a dynamic IP, subnet mask, gateway, and DNS server from a DHCP server.

### Static IP Configuration for Servers and Cameras
`pancakes` are configured to have user friendly names for ports that are easy to understand, document, and find on the machine. Names such as `mlnx1_p1_25g` mean:
- `mlnx1`: Mellanox Card Card (on the given machine)
- `p1_25g`: Port on the card at the 25Gbps speed
- `match`/`macaddress`
Specifies to match the exact physical interface via MAC address.
- `dhcp4: false`
Disables IPv4 DHCP so we can assign a specific IP address.
- `dhcp6: false`
Disables IPv6 DHCP so we can assign a specific IP address.
- `addresses: [192.168.110.1/24]`
This specifies a static IPv4 address for the device of 192.168.110.1 with a subnet mask of 24 that ultimately yields 254 usable host addresses.
- `mtu: 9000`
Sets the Maximum Transmission Unit (MTU) to 9000 bytes, enabling jumbo frames for improved performance in our high-speed networks. A jumbo frame is any ethernet packet/frame with a payload larger than 1500 bytes.

Now that you have these configured, simply type:

`sudo netplan apply --debug`

This will configure your network interfaces to use the appropriate IPs and MTU settings upon the next reboot.

## Reboot the server

Finally, reboot the server. You can simply type in the terminal:

`sudo reboot`

Once the machine has started again, check that things have worked with:

`ip addr show`
