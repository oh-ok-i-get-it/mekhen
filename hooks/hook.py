"""Claude Code hook entry point: runs the mekhen bundled with this plugin (standard library only),
so the hooks and the viewer they start are always the same version."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mekhen.cli.hook import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
