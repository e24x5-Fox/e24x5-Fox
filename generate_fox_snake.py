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
import math
import time
from collections import deque
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from PIL import Image

CELL = 12          # px per grid cell (output scale)
GAP = 2             # px gap between cells
CELL_RADIUS = 2      # corner radius, matches GitHub's own contribution graph
FOX_SCALE = 1        # scale factor applied to the 8 walk-cycle frames
FRAME_DIR = "assets/fox_frames"
N_WALK_FRAMES = 8

PALETTES = {
    "light": {
        "level_colors": {
            0: "#ebedf0",
            1: "#9be9a8",
            2: "#40c463",
            3: "#30a14e",
            4: "#216e39",
        },
        "eaten_color": "#d0d7de",
        "bg_color": "#ffffff",
        "text_color": "#656d76",
    },
    "dark": {
        "level_colors": {
            0: "#151b23",
            1: "#033a16",
            2: "#196c2e",
            3: "#2ea043",
            4: "#56d364",
        },
        "eaten_color": "#30363d",
        "bg_color": "#0d1117",
        "text_color": "#9198a1",
    },
}

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}  # row index (0=Sun) -> label
LABEL_FONT = (
    'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" '
    'font-size="9"'
)


def img_to_base64(img):
    """Convert PIL Image to base64 encoded PNG string."""
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def fetch_contributions(username: str, retries: int = 3, backoff: float = 2.0):
    """Scrape the public contribution calendar (same endpoint snk uses)."""
    url = f"https://github.com/users/{username}/contributions"
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url, headers={"User-Agent": "fox-snake-generator"}, timeout=15
            )
            resp.raise_for_status()
            break
        except (requests.RequestException,) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    else:
        raise RuntimeError(
            f"Failed to fetch contributions for '{username}' after {retries} attempts"
        ) from last_error

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


def build_animation(cells, out_path, palette="light"):
    level_colors = PALETTES[palette]["level_colors"]
    eaten_color = PALETTES[palette]["eaten_color"]
    bg_color = PALETTES[palette]["bg_color"]
    text_color = PALETTES[palette]["text_color"]

    max_col = max(c["col"] for c in cells)
    max_row = max(c["row"] for c in cells)
    grid_w = (max_col + 1) * (CELL + GAP)
    grid_h = (max_row + 1) * (CELL + GAP)

    outer_margin = 10
    day_label_width = 24   # room for "Mon"/"Wed"/"Fri" to the left of the grid
    month_label_height = 16  # room for month names above the grid
    strip_gap = 8           # gap between grid and the eaten-order progress strip
    strip_height = CELL

    grid_x0 = outer_margin + day_label_width
    grid_y0 = outer_margin + month_label_height

    canvas_w = grid_x0 + grid_w + outer_margin
    canvas_h = grid_y0 + grid_h + strip_gap + strip_height + outer_margin

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
        x = grid_x0 + c["col"] * (CELL + GAP) + CELL / 2
        y = grid_y0 + c["row"] * (CELL + GAP) + CELL / 2
        return x, y

    # Grid-walk pathfinding: the fox may only move to an orthogonally
    # adjacent day, and only onto a not-yet-eaten activity cell if that is
    # the cell it is currently heading for — everything else with activity
    # is a wall until it's its turn, same as the original snake routing
    # around days it isn't eating yet.
    cell_by_rc = {(c["row"], c["col"]): i for i, c in enumerate(cells)}
    blocked_rc = {(c["row"], c["col"]) for c in cells if c["level"] > 0}

    def find_path(start_rc, target_rc):
        if start_rc == target_rc:
            return [start_rc]
        visited = {start_rc}
        parent = {}
        queue = deque([start_rc])
        while queue:
            cur = queue.popleft()
            if cur == target_rc:
                break
            r, c = cur
            for nxt in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if nxt in visited or nxt not in cell_by_rc:
                    continue
                if nxt != target_rc and nxt in blocked_rc:
                    continue
                visited.add(nxt)
                parent[nxt] = cur
                queue.append(nxt)
        if target_rc not in parent and target_rc != start_rc:
            return None
        path = [target_rc]
        while path[-1] != start_rc:
            path.append(parent[path[-1]])
        path.reverse()
        return path

    # Smart eating order
    order = []
    by_level = {}
    for i, c in enumerate(cells):
        if c["level"] == 0:
            continue
        by_level.setdefault(c["level"], []).append(i)

    if not by_level:
        raise RuntimeError("No non-zero contribution days found.")

    # Nearest-neighbour selection starts from the grid's top-left cell (the
    # earliest date), same starting point the fox itself walks from.
    current_xy = cell_xy(cells[0])
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
    # px covered per substep, calibrated so a single grid-cell hop keeps the
    # original pacing; used to give every hop the same on-screen speed
    # regardless of how far apart the two cells are.
    PX_PER_SUBSTEP = (CELL + GAP) / SUB_FRAMES

    prev_xy = cell_xy(cells[0])
    walk_counter = 0
    cell_eaten_time = {}

    def walk_to(prev_xy, target_xy, walk_counter, current_time, on_arrive=None):
        distance = math.hypot(target_xy[0] - prev_xy[0], target_xy[1] - prev_xy[1])
        n_sub = max(1, round(distance / PX_PER_SUBSTEP))
        facing_left = target_xy[0] < prev_xy[0]

        for sub in range(n_sub):
            t = (sub + 1) / n_sub
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

            if sub == n_sub - 1 and on_arrive:
                on_arrive(current_time)

        return target_xy, walk_counter, current_time

    # 1. Фаза поедания ячеек — лиса стартует из самой первой (верхней
    # левой) клетки года и идёт по сетке шаг за шагом, обходя ещё не
    # съеденные клетки активности, наступая на клетку только тогда,
    # когда пришла её очередь быть съеденной.
    current_rc = (cells[0]["row"], cells[0]["col"])
    for cell_idx in order:
        target_rc = (cells[cell_idx]["row"], cells[cell_idx]["col"])
        path = find_path(current_rc, target_rc)
        if path is None:
            # Полностью окружена ещё не съеденными клетками (редкий
            # случай) — идём напрямую, а не застреваем.
            path = [current_rc, target_rc]

        waypoints = path[1:] or [target_rc]
        for i, rc in enumerate(waypoints):
            target_xy = cell_xy(cells[cell_by_rc[rc]])
            is_last = i == len(waypoints) - 1
            prev_xy, walk_counter, current_time = walk_to(
                prev_xy, target_xy, walk_counter, current_time,
                on_arrive=(
                    (lambda t, idx=cell_idx: cell_eaten_time.__setitem__(idx, t))
                    if is_last else None
                ),
            )

        blocked_rc.discard(target_rc)
        current_rc = target_rc

    # 2. Возврат к стартовой клетке для плавного зацикливания — тем же
    # поиском пути по сетке (только вверх/вниз/влево/вправо, без
    # диагоналей); к этому моменту все клетки активности уже съедены,
    # так что путь ничем не заблокирован.
    start_rc = (cells[0]["row"], cells[0]["col"])
    if current_rc != start_rc:
        path_back = find_path(current_rc, start_rc) or [current_rc, start_rc]
        for rc in path_back[1:]:
            target_xy = cell_xy(cells[cell_by_rc[rc]])
            prev_xy, walk_counter, current_time = walk_to(
                prev_xy, target_xy, walk_counter, current_time
            )

    # 3. Пауза в самом конце (лиса сидит на стартовой ячейке): 6 кадров по 200мс
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
        x = grid_x0 + c["col"] * (CELL + GAP)
        y = grid_y0 + c["row"] * (CELL + GAP)
        original_color = level_colors.get(c["level"], level_colors[0])

        if i in cell_eaten_time:
            t_eaten = cell_eaten_time[i] / total_duration
            cells_svg_parts.append(
                f'  <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="{CELL_RADIUS}" ry="{CELL_RADIUS}" fill="{original_color}">\n'
                f'    <animate attributeName="fill" calcMode="discrete" dur="{total_duration:.2f}s" '
                f'repeatCount="indefinite" values="{original_color};{eaten_color}" keyTimes="0;{t_eaten:.4f}" />\n'
                f'  </rect>'
            )
        else:
            cells_svg_parts.append(
                f'  <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="{CELL_RADIUS}" ry="{CELL_RADIUS}" fill="{original_color}" />'
            )
    cells_svg_str = "\n".join(cells_svg_parts)

    # Month labels above the grid: one per column where the month changes
    col_first_date = {}
    for c in cells:
        col_first_date.setdefault(c["col"], c["date"])

    month_label_parts = []
    last_month = None
    last_label_col = None
    for col in sorted(col_first_date):
        month = int(col_first_date[col][5:7])
        if month != last_month and (last_label_col is None or col - last_label_col >= 3):
            x = grid_x0 + col * (CELL + GAP)
            y = grid_y0 - 6
            month_label_parts.append(
                f'  <text x="{x}" y="{y}" fill="{text_color}" {LABEL_FONT}>{MONTH_ABBR[month - 1]}</text>'
            )
            last_label_col = col
        last_month = month
    month_labels_svg = "\n".join(month_label_parts)

    # Day-of-week labels to the left of the grid
    day_label_parts = []
    for row, label in DAY_LABELS.items():
        x = outer_margin
        y = grid_y0 + row * (CELL + GAP) + CELL / 2 + 3
        day_label_parts.append(
            f'  <text x="{x}" y="{y}" fill="{text_color}" {LABEL_FONT}>{label}</text>'
        )
    day_labels_svg = "\n".join(day_label_parts)

    # Progress strip: one segment per eaten cell, lighting up with that
    # cell's activity color the moment the fox eats it — same idea as the
    # original snake's fill-up bar.
    strip_y = grid_y0 + grid_h + strip_gap
    seg_w = grid_w / len(order)
    strip_parts = []
    for i, cell_idx in enumerate(order):
        seg_x = grid_x0 + i * seg_w
        seg_color = level_colors.get(cells[cell_idx]["level"], level_colors[0])
        t_eaten = cell_eaten_time[cell_idx] / total_duration
        strip_parts.append(
            f'  <rect x="{seg_x:.2f}" y="{strip_y}" width="{seg_w + 0.5:.2f}" height="{strip_height}" '
            f'rx="{CELL_RADIUS}" ry="{CELL_RADIUS}" fill="{eaten_color}">\n'
            f'    <animate attributeName="fill" calcMode="discrete" dur="{total_duration:.2f}s" '
            f'repeatCount="indefinite" values="{eaten_color};{seg_color}" keyTimes="0;{t_eaten:.4f}" />\n'
            f'  </rect>'
        )
    strip_svg = "\n".join(strip_parts)

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
  <rect width="{canvas_w}" height="{canvas_h}" fill="{bg_color}" />
{month_labels_svg}
{day_labels_svg}
{cells_svg_str}
{strip_svg}
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
    ap.add_argument(
        "--dark-out",
        default=None,
        help="Path for the dark-theme variant (default: <out>-dark.svg)",
    )
    ap.add_argument(
        "--no-dark",
        action="store_true",
        help="Skip generating the dark-theme variant",
    )
    args = ap.parse_args()

    cells = fetch_contributions(args.user)
    build_animation(cells, args.out, palette="light")

    if not args.no_dark:
        dark_out = args.dark_out
        if dark_out is None:
            base, ext = args.out.rsplit(".", 1)
            dark_out = f"{base}-dark.{ext}"
        build_animation(cells, dark_out, palette="dark")


if __name__ == "__main__":
    main()
