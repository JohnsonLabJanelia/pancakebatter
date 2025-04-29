# PDU Installation

Download the CyberPower_PDNU2_Linux_64bit_v2.1.2.sh

Move the .sh to /opt/ and make executable
Run executable with sudo bash CyberPower_PDNU2_Linux_64bit_v2.1.2.sh
Follow prompts to let it do its thing


## Connect to PDU Monitor
Default is localhost:8084
Go to this in the browser

## Add your PDU
PDU may already appear in the web app, but is not yet properly added
Hit + button and add the PDU with the default IP. Since this is not directly connected to the network, you will not have a dynamically assigned IP. You'll instead use the default of:
192.168.20.177

Click on that IP or navigate to it in the browser's bar.

## "Log into" the PDU
The PDU has its own CLI that you can use via SSH (and other methods if you want)
The PDU will require you to "log into" it via a username and password. As of 4/28/25, you may need to try the following username/password combos (same word for both)
- cyber
- admin
- test

You'll then have full access to the PDU and its functions.

## Ratan's Rig Utils
Use Ratan's rig utils scripts for templates on how to communicate with your PDU, make customized scripts for your PDU's IP address.


## Username/pass stored in a secret place. Ask Jeremy for it if you really need it.

## Run the CLI Tool pdu.py like this:
./pdu.py --host 192.168.20.177 --action on --outlet 1,2,3,4

## Or like this:
./pdu.py --host 192.168.20.177 --action off --outlet all

## Or this:
./pdu.py --host 192.168.20.177 --action reboot --outlet all