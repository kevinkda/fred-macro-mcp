"""Console-script entry point.

``python -m fred_macro_mcp`` and the ``fred-macro-mcp`` script both land
here, which delegates to :func:`fred_macro_mcp.server.main`.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    main()
