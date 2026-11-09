#!/usr/bin/env python3
"""
The file modes the framework sets on what others must read or run, named by what they allow and built from the stat
bits (so every mode says who may read, write and run, instead of an octal number to decode).

    SHARED_READ    0644  a file other accounts read (the build profile the container's user reads, a scan archive)
    SHARED_RUN     0755  a folder other accounts enter, or a program they run (deploy.sh, a generated build script)
    READ_ONLY      0444  a file nobody changes in place (a script of the runtime layer)
    READ_RUN_ONLY  0555  a program or folder nobody changes in place (a tool or folder of the runtime layer)
    NEW_FILE       0666  the mode a new file is created with before the umask (what open() and Path.write_text use)

Private files and folders (0600, 0700) are never shared and keep their own stat bits where they are made.
"""

import stat

_READ_ALL = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
_RUN_ALL = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
_WRITE_ALL = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH

READ_ONLY = _READ_ALL
READ_RUN_ONLY = _READ_ALL | _RUN_ALL
SHARED_READ = _READ_ALL | stat.S_IWUSR
SHARED_RUN = SHARED_READ | _RUN_ALL
NEW_FILE = _READ_ALL | _WRITE_ALL
