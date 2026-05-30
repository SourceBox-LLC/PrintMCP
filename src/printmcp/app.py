"""The shared FastMCP application instance.

Kept in its own module so tool modules can ``from .app import mcp`` without an
import cycle with the server entry point in ``server.py``.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("printmcp")
