import asyncio
import logging
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


def _wsl_to_win(wsl_path: str) -> str:
    """Convert /mnt/c/... to C:\\..."""
    if wsl_path.startswith("/mnt/"):
        parts = wsl_path[5:].split("/", 1)
        drive = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        return f"{drive}:\\{rest}".replace("/", "\\")
    return wsl_path


async def take_screenshot() -> tuple[str | None, str | None]:
    """Capture Windows screen from WSL via PowerShell. Returns (wsl_path, error)."""
    wsl_path = str(Config.WORKSPACE / "screenshot.png")
    win_path = _wsl_to_win(wsl_path)

    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screens = [System.Windows.Forms.Screen]::AllScreens
$top = ($screens | ForEach-Object {{ $_.Bounds.Top }} | Measure-Object -Minimum).Minimum
$left = ($screens | ForEach-Object {{ $_.Bounds.Left }} | Measure-Object -Minimum).Minimum
$width = ($screens | ForEach-Object {{ $_.Bounds.Right }} | Measure-Object -Maximum).Maximum
$height = ($screens | ForEach-Object {{ $_.Bounds.Bottom }} | Measure-Object -Maximum).Maximum
$bounds = [System.Drawing.Rectangle]::FromLTRB($left, $top, $width, $height)
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save('{win_path}')
$g.Dispose()
$bmp.Dispose()
"""

    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            return None, f"Screenshot failed: {error[:300]}"

        if Path(wsl_path).exists():
            return wsl_path, None
        return None, "Screenshot file not created"
    except asyncio.TimeoutError:
        return None, "Screenshot timed out"
    except FileNotFoundError:
        return None, "powershell.exe not found - are you in WSL?"
    except Exception as e:
        return None, str(e)
