"""Generate an animated demo GIF of Tezcat — preset selection through report.

Captures key moments from a flash-crash run and stitches them into a 12-second
GIF at 3 frames per second (36 frames total for smooth playback).

Usage: .venv/bin/python scripts/make_demo_gif.py
"""

import sys
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
RUN_ID = "run_27483f40"  # the completed flash-crash run

FRAMES = []
FPS = 3


def capture(url, label, delay=0.5, n_frames=1):
    """Capture one or more frames."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 700}, device_scale_factor=1.0)
        page.goto(f"{BASE}/{url}")
        page.wait_for_load_state("networkidle")
        time.sleep(delay)
        for i in range(n_frames):
            img_bytes = page.screenshot()
            img = annotate_frame(img_bytes, label)
            FRAMES.append(img)
            if i < n_frames - 1:
                time.sleep(0.2)
        browser.close()


def annotate_frame(img_bytes, label):
    """Add a caption to the screenshot; return annotated Image."""
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    # Simple caption at the top in monospace
    try:
        font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    # black text on semi-transparent bg
    text = f"  {label}  "
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([(0, 0), (w + 4, h + 4)], fill=(0, 0, 0, 200))
    draw.text((2, 2), text, fill=(200, 140, 50), font=font)  # amber text
    return img


if __name__ == "__main__":
    print("Capturing frames...")

    # Page 1: Preset gallery (show for 2 sec = 6 frames)
    capture("#/", "1. PRESET GALLERY  →  select FLASH CRASH", delay=1.5, n_frames=6)

    # Page 2: Experiment detail (1 sec = 3 frames)
    capture("#/experiment/exp_ccd9a16e", "2. CREATE EXPERIMENT  →  START RUN", delay=0.3, n_frames=3)

    # Page 3: Live run before crash (2 sec = 6 frames, showing step progression)
    capture(f"#/run/{RUN_ID}", "3. RUN STARTS  —  stable market building", delay=0.5, n_frames=6)

    # Page 4: Live run after crash (show the crash region, 2 sec = 6 frames)
    # (same URL, just waits longer so we're looking at the post-shock period)
    capture(f"#/run/{RUN_ID}", "4. WHALE SHOCK FIRED  —  CRISIS regime", delay=1.5, n_frames=6)

    # Page 5: Recovery phase (show later in the run, 2 sec = 6 frames)
    capture(f"#/run/{RUN_ID}", "5. RECOVERY  —  mean-reversion buying", delay=2.0, n_frames=6)

    # Page 6: Report page (2 sec = 6 frames)
    capture(f"#/run/{RUN_ID}/report", "6. REPORT  —  +29.4K drawdown  crash=YES", delay=0.8, n_frames=6)

    print(f"Captured {len(FRAMES)} frames")

    # All frames are already RGB
    frames_rgb = FRAMES

    # Save as GIF
    output = Path("docs/img/tezcat_demo.gif")
    frames_rgb[0].save(
        output,
        save_all=True,
        append_images=frames_rgb[1:],
        duration=int(1000 / FPS),  # 333ms per frame at 3 FPS
        loop=0,
    )
    print(f"Saved {output}")
