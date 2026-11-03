"""Entry point for PyInstaller (the package's __main__ uses relative imports)."""

import sys

from jarvis_panel.app import main

sys.exit(main())
