"""File modes the tests set up, named by their octal value and built from the stat bits.

A test that gives a file or folder a mode (a key others could read, a folder anyone may write, a program with the
set-user-ID bit) uses these, so the mode reads as the number it is; test_file_modes checks every value.
"""

import stat

_R = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
_X = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

MODE_444 = _R
MODE_555 = _R | _X
MODE_640 = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP
MODE_644 = _R | stat.S_IWUSR
MODE_666 = _R | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
MODE_750 = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
MODE_755 = MODE_644 | _X
MODE_775 = stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH
MODE_777 = stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
MODE_070 = stat.S_IRWXG
MODE_4755 = stat.S_ISUID | MODE_755
