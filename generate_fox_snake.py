"""
generate_fox_snake.py

Fetches a GitHub user's real contribution calendar and generates an
animated GIF where a fox sprite walks across the grid, eating cells
in order from LEAST contributions to MOST (same ordering logic as the
original Platane/snk snake).

Usage:
    python3 generate_fox_snake.py --user e24x5-Fox --out fox-snake.gif

Requires: requests, beautifulsoup4, pillow
    pip install requests beautifulsoup4 pillow
"""

import argparse
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw

CELL = 12          # px per grid cell (output scale)
GAP = 2             # px gap between cells
FOX_SCALE = 3        # scale factor applied to the 8 walk-cycle frames
FRAME_DIR = "assets/fox_frames"
N_WALK_FRAMES = 8

LEVEL_COLORS = {
    0: "#ebedf0",
    1: "#9be9a8",
    2: "#40c463",
    3: "#30a14e",
    4: "#216e39",
}
EATEN_COLOR = "#d0d7de"


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def fetch_contributions(username: str):
    """Scrape the public contribution calendar (same endpoint snk uses)."""
    url = f"https://github.com/users/{username}/contributions"
    resp = requests.get(url, headers={"User-Agent": "fox-snake-generator"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cells = []
    # current GitHub markup uses <td class="ContributionCalendar-day" data-date="..." data-level="...">
    for td in soup.select("td[data-date]"):
        date_str = td.get("data-date")
        level = td.get("data-level")
        if level is None:
            # fallback: derive level from fill/class if data-level missing
            level = 0
        else:
            level = int(level)
        cells.append({"date": date_str, "level": level})

    if not cells:
        raise RuntimeError(
            "No contribution cells found — GitHub may have changed its markup. "
            "Check the parsing selectors in fetch_contributions()."
        )

    # assign grid coordinates: column = week index, row = weekday (Sun=0)
    cells.sort(key=lambda c: c["date"])
    first_date = datetime.strptime(cells[0]["date"], "%Y-%m-%d")
    start_weekday = (first_date.weekday() + 1) % 7  # convert Mon=0 -> Sun=0

    for i, c in enumerate(cells):
        offset = i + start_weekday
        c["col"] = offset // 7
        c["row"] = offset % 7

    return cells


def build_animation(cells, out_path):
    max_col = max(c["col"] for c in cells)
    max_row = max(c["row"] for c in cells)
    grid_w = (max_col + 1) * (CELL + GAP)
    grid_h = (max_row + 1) * (CELL + GAP)

    margin = 20
    canvas_w = grid_w + margin * 2
    canvas_h = grid_h + margin * 2

    fox_frames = [
        Image.open(f"{FRAME_DIR}/frame_{i}.png").convert("RGBA")
        for i in range(N_WALK_FRAMES)
    ]
    fox_frames = [
        f.resize((f.width * FOX_SCALE, f.height * FOX_SCALE), Image.NEAREST)
        for f in fox_frames
    ]
    fw, fh = fox_frames[0].size

    # eating order: ascending contribution level, ties broken chronologically
    order = sorted(range(len(cells)), key=lambda i: (cells[i]["level"], cells[i]["date"]))

    frames_out = []
    durations = []
    eaten = set()

    STEP_FRAMES = 1  # how many animation frames per grid cell (higher = slower/smoother, but bigger file)

    for step_i, cell_idx in enumerate(order):
        eaten.add(cell_idx)
        cell = cells[cell_idx]
        cx = margin + cell["col"] * (CELL + GAP)
        cy = margin + cell["row"] * (CELL + GAP)

        for sub in range(STEP_FRAMES):
            canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
            draw = ImageDraw.Draw(canvas)

            for i, c in enumerate(cells):
                x = margin + c["col"] * (CELL + GAP)
                y = margin + c["row"] * (CELL + GAP)
                color = EATEN_COLOR if i in eaten else LEVEL_COLORS.get(c["level"], "#ebedf0")
                draw.rounded_rectangle(
                    [x, y, x + CELL, y + CELL], radius=2, fill=hex2rgb(color)
                )

            walk_frame = fox_frames[(step_i * STEP_FRAMES + sub) % N_WALK_FRAMES]
            fox_x = cx + CELL // 2 - fw // 2
            fox_y = cy + CELL // 2 - fh // 2
            canvas.paste(walk_frame, (fox_x, fox_y), walk_frame)

            frames_out.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=32))
            durations.append(70)

    # hold last frame briefly before looping
    for _ in range(6):
        frames_out.append(frames_out[-1])
        durations.append(200)

    frames_out[0].save(
        out_path,
        save_all=True,
        append_images=frames_out[1:],
        duration=durations,
        loop=0,
        disposal=2,
    )
    print(f"saved {out_path} ({len(frames_out)} frames, {len(cells)} cells)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="fox-snake.gif")
    args = ap.parse_args()

    cells = fetch_contributions(args.user)
    build_animation(cells, args.out)


if __name__ == "__main__":
    main()
