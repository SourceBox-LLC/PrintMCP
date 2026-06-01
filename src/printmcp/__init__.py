"""PrintMCP - an MCP server for automating the 3D printing pipeline.

Three levels, all implemented:
- Level 1: search for and download 3D models (Thingiverse).
- Level 2: slice models into G-code (Ultimaker Cura / CuraEngine).
- Level 3: manage and control printing (OctoPrint).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("printmcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
