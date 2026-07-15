"""
generate_fox_snake.py

Fetches a GitHub user's real contribution calendar and generates an
animated SVG where a fox sprite walks across the grid, eating cells
in order from LEAST contributions to MOST (same ordering logic as the
original Platane/snk snake).

Usage:
    python3 generate_fox_snake.py --user e24x5-Fox --out fox-snake.svg

Requires: requests, beautifulsoup4, pillow
    pip install requests beautifulsoup4 pillow
"""

import argparse
import base64
import io
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from PIL import Image

CELL = 12          # px per grid cell (output scale)
GAP = 2             # px gap between cells
FOX_SCALE = 2        # scale factor applied to the 8 walk-cycle frames
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


def img_to_base64(img):
    """Convert PIL Image to base64 encoded PNG string."""
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def fetch_contributions(username: str):
    """Scrape the public contribution calendar (same endpoint snk uses)."""
    url = f"https://github.com/users/{username}/contributions"
    resp = requests.get(url, headers={"User-Agent": "fox-snake-generator"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cells = []
    for td in soup.select("td[data-date]"):
        date_str = td.get("data-date")
        level = td.get("data-level")
        if level is None:
            level = 0
        else:
            level = int(level)
        cells.append({"date": date_str, "level": level})

    if not cells:
        raise RuntimeError(
            "No contribution cells found — GitHub may have changed its markup."
        )

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

    # Prepare fox frames and encode to base64
    fox_frames_r = [
        Image.open(f"{FRAME_DIR}/frame_{i}.png").convert("RGBA")
        for i in range(N_WALK_FRAMES)
    ]
    fox_frames_r = [
        f.resize((f.width * FOX_SCALE, f.height * FOX_SCALE), Image.NEAREST)
        for f in fox_frames_r
    ]
    fox_frames_l = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in fox_frames_r]
    fw, fh = fox_frames_r[0].size

    base64_r = [img_to_base64(f) for f in fox_frames_r]
    base64_l = [img_to_base64(f) for f in fox_frames_l]

    def cell_xy(c):
        x = margin + c["col"] * (CELL + GAP) + CELL / 2
        y = margin + c["row"] * (CELL + GAP) + CELL / 2
        return x, y

    # Smart eating order
    order = []
    by_level = {}
    for i, c in enumerate(cells):
        if c["level"] == 0:
            continue
        by_level.setdefault(c["level"], []).append(i)

    if not by_level:
        raise RuntimeError("No non-zero contribution days found.")

    first_idx = by_level[sorted(by_level.keys())[0]][0]
    current_xy = cell_xy(cells[first_idx])
    for level in sorted(by_level.keys()):
        remaining = by_level[level][:]
        while remaining:
            best_j, best_dist = 0, None
            for j, idx in enumerate(remaining):
                x, y = cell_xy(cells[idx])
                d = (x - current_xy[0]) ** 2 + (y - current_xy[1]) ** 2
                if best_dist is None or d < best_dist:
                    best_dist, best_j = d, j
            idx = remaining.pop(best_j)
            order.append(idx)
            current_xy = cell_xy(cells[idx])

    # Build steps for the timeline
    timeline = []
    current_time = 0.0
    SUB_FRAMES = 2
    dt = 0.09  # 90ms per step

    prev_xy = cell_xy(cells[order[0]])
    walk_counter = 0
    cell_eaten_time = {}

    for step_i, cell_idx in enumerate(order):
        cell = cells[cell_idx]
        target_xy = cell_xy(cell)
        facing_left = target_xy[0] < prev_xy[0]

        for sub in range(SUB_FRAMES):
            t = (sub + 1) / SUB_FRAMES
            fox_cx = prev_xy[0] + (target_xy[0] - prev_xy[0]) * t
            fox_cy = prev_xy[1] + (target_xy[1] - prev_xy[1]) * t

            fox_x = int(fox_cx - fw / 2)
            fox_y = int(fox_cy - fh / 2)

            frame_idx = walk_counter % N_WALK_FRAMES
            active_frame_id = (facing_left, frame_idx)
            walk_counter += 1

            timeline.append({
                "time": current_time,
                "translate": (fox_x, fox_y),
                "active_frame_id": active_frame_id
            })
            current_time += dt

            if sub == SUB_FRAMES - 1:
                cell_eaten_time[cell_idx] = current_time

        prev_xy = target_xy

    # Pause at the end: 6 frames of 200ms
    for _ in range(6):
        timeline.append({
            "time": current_time,
            "translate": timeline[-1]["translate"],
            "active_frame_id": timeline[-1]["active_frame_id"]
        })
        current_time += 0.20

    total_duration = current_time

    # Generate keyTimes and translate values
    key_times = [item["time"] / total_duration for item in timeline]
    key_times_str = ";".join(f"{t:.4f}" for t in key_times)
    values_translate = ";".join(f"{item['translate'][0]},{item['translate'][1]}" for item in timeline)

    # Build animated background cells
    cells_svg_parts = []
    for i, c in enumerate(cells):
        x = margin + c["col"] * (CELL + GAP)
        y = margin + c["row"] * (CELL + GAP)
        original_color = LEVEL_COLORS.get(c["level"], "#ebedf0")

        if i in cell_eaten_time:
            t_eaten = cell_eaten_time[i] / total_duration
            cells_svg_parts.append(
                f'  <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="{original_color}">\n'
                f'    <animate attributeName="fill" calcMode="discrete" dur="{total_duration:.2f}s" '
                f'repeatCount="indefinite" values="{original_color};{EATEN_COLOR}" keyTimes="0;{t_eaten:.4f}" />\n'
                f'  </rect>'
            )
        else:
            cells_svg_parts.append(
                f'  <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="{original_color}" />'
            )
    cells_svg_str = "\n".join(cells_svg_parts)

    # Build base64 image nodes for Fox
    images_str = []
    # Right-facing images
    for idx, b64 in enumerate(base64_r):
        vals = ";".join("1" if item["active_frame_id"] == (False, idx) else "0" for item in timeline)
        images_str.append(
            f'    <image x="0" y="0" width="{fw}" height="{fh}" href="data:image/png;base64,{b64}" opacity="0">\n'
            f'      <animate attributeName="opacity" calcMode="discrete" dur="{total_duration:.2f}s" '
            f'repeatCount="indefinite" values="{vals}" keyTimes="{key_times_str}" />\n'
            f'    </image>'
        )
    # Left-facing images
    for idx, b64 in enumerate(base64_l):
        vals = ";".join("1" if item["active_frame_id"] == (True, idx) else "0" for item in timeline)
        images_str.append(
            f'    <image x="0" y="0" width="{fw}" height="{fh}" href="data:image/png;base64,{b64}" opacity="0">\n'
            f'      <animate attributeName="opacity" calcMode="discrete" dur="{total_duration:.2f}s" '
            f'repeatCount="indefinite" values="{vals}" keyTimes="{key_times_str}" />\n'
            f'    </image>'
        )
    images_svg_str = "\n".join(images_str)

    # Assemble SVG
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}">
  <rect width="{canvas_w}" height="{canvas_h}" fill="#ffffff" />
{cells_svg_str}
  <g id="fox">
    <animateTransform attributeName="transform" type="translate" calcMode="discrete" dur="{total_duration:.2f}s" repeatCount="indefinite" values="{values_translate}" keyTimes="{key_times_str}" />
{images_svg_str}
  </g>
</svg>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg_content)

    print(f"saved SVG: {out_path} ({len(timeline)} frames, {len(cells)} cells)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="fox-snake.svg")
    args = ap.parse_args()

    cells = fetch_contributions(args.user)
    build_animation(cells, args.out)


if __name__ == "__main__":
    main()
