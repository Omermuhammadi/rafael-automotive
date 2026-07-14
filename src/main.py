"""Application entry point.

Run from the project root::

    python -m src.main
"""

from __future__ import annotations

import tkinter

from src.app.shell import build_app


def main() -> None:
    """Build the shell and run the Tk event loop.

    A fresh ``Tk()`` re-initialises Tcl, which on Windows can intermittently fail to read
    ``init.tcl`` if an antivirus/indexer momentarily locks the file. That is transient and
    harmless, so we retry the build once before surfacing the error.
    """
    try:
        app = build_app()
    except tkinter.TclError:
        app = build_app()  # one retry: the Tcl-init hiccup does not repeat
    app.mainloop()


if __name__ == "__main__":
    main()
