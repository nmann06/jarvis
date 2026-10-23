"""Start the Jarvis window (jarvis_code/) from this folder: `python -I launch.py [options]`.

Run isolated (-I) so files in the folder you open it from can't shadow its modules; -I also
leaves this folder off sys.path, so it goes on here. jarvis-code runs this on hyde, and
`jarvis` in PowerShell runs it on the Windows laptop (deploy/jarvis.ps1).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis_code.__main__ import main  # noqa: E402

sys.exit(main())
