# pancakebatter

The purpose of this repository is to combine the documentation created by [Jinyao Yan, PhD](https://www.janelia.org/people/jinyao-yan) (Johnson Lab Research Associate) and
[Ratan Othayoth, PhD](https://www.janelia.org/people/ratan-othayoth-0) (Johnson Lab Research Associate) into one document for new rigs built by Jeremy Delahanty for use in
group leaders [Misha Ahrens, PhD](https://ahrenslab.org/) and [Rob Johnson, PhD](https://www.janelia.org/lab/johnson-lab) labs (but also those elsewhere
in Janelia or even the world one day!).

A secondary goal is to introduce some basic automation into installing required packages, drivers, and static versioning for rig building in the lab. There will also be a
standard directory structure built on the lab's server containing relevant files, documentation, and configuration files that are used for checking whether all installations
meet a given known working configuration.

Longer term plans could include utilizing tools like [spack](https://spack.readthedocs.io/en/latest/index.html) and [Warewolf](https://warewolf.io/index.php) so its very well
documented and maintained with infra as code. That's very low priority for now.

As of 9/30/24:

- NVIDIA Driver Successfully Installed
- BIOS Updated for using all GPUs and USB ports, BAR Support, grub updated
- Kernel and Ubuntu Image Held (frozen in place)
- Emergent eSDK and Mellanox/EVT drivers installed via .deb and .sh installer
- VSCode and Chrome added to apt repository
