"""The self-built llama.cpp runtime on a device (design 4.4, decisions D7, D14, D22 and 8).

The device's own probe profile (``llm-cli scan --device``) decides what is built for it: a cross build in the
builder container whose base matches the device's C library and version (package.plan_device_runtime). The verified
build becomes a tarball with the programs, the OpenMP runtime when the build uses it, the start script and the
llama.cpp licence (package.py). It is deployed over SSH into the device user's home (remote.py) with the start script
of launcher.py (loopback, a key file generated on the device under umask 077, a cleared environment, the shared
forbidden-option list) and, when the deploy settings say so, a systemd unit (settings.py: chosen before the
deployment, never asked at run time). The programs are then checked on the device as the unprivileged user:
version, devices and, with a model on the device, a short llama-bench run.
"""
