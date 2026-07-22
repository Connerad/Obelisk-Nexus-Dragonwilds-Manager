from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path


class ImageService:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_png(self, url: str) -> Path | None:
        if not url:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        png = self.cache_dir / f"{key}.png"
        if png.exists() and png.stat().st_size > 100:
            return png
        original = self.cache_dir / f"{key}.image"
        request = urllib.request.Request(url, headers={"User-Agent": "DragonwildsServerManagerRebuild/1.0.11", "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8", "Referer": "https://www.nexusmods.com/runescapedragonwilds/mods/"})
        with urllib.request.urlopen(request, timeout=20) as response, original.open("wb") as out:
            shutil.copyfileobj(response, out, 256 * 1024)
        # Native PNG/GIF can be copied; JPEG/WebP are converted by a platform tool.
        header = original.read_bytes()[:12]
        if header.startswith(b"\x89PNG"):
            original.replace(png)
            return png
        if os.name == "nt":
            script = (
                "Add-Type -AssemblyName System.Drawing; "
                "$i=[System.Drawing.Image]::FromFile($args[0]); "
                "$i.Save($args[1],[System.Drawing.Imaging.ImageFormat]::Png); $i.Dispose()"
            )
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(original), str(png)], capture_output=True, timeout=30)
        else:
            tool = shutil.which("magick") or shutil.which("convert")
            if not tool:
                return None
            result = subprocess.run([tool, str(original), "-resize", "128x128>", str(png)], capture_output=True, timeout=30)
        original.unlink(missing_ok=True)
        if result.returncode != 0 or not png.exists():
            png.unlink(missing_ok=True)
            return None
        return png
