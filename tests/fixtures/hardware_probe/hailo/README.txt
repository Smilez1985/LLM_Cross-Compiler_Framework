Hailo outputs for tests/test_hardware_probe_keys.py (HAILO_PLAN.md 6.1 item 1).

identify_*_docs.txt: the samples published by Hailo (hailo-rpi5-examples doc/install-raspberry-pi5.md, hailo_model_zoo
docs/GETTING_STARTED.rst, tappas docs/installation/verify_hailoRT.rst); serial and part numbers replaced by made-up
values. identify_current_*.txt: synthetic, from the format strings of hailortcli fw_control_command.cpp of HailoRT
4.24.0 and 5.4.0 (no "Board Name" line). scan_*.txt from scan_command.cpp and pcie_utils.cpp / usb_utils_common.cpp
(5.x: pci/ and usb/ prefixes and [integrated]; 4.x: the bare PCI address). version_*.txt from hailortcli.cpp: the
mismatch message goes to stderr with exit code -1 (255).
