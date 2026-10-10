"""Draw a two-storey school building floor plan through the AutoCAD MCP server.

Launches the server over MCP stdio (as an MCP client would) and builds 1F and 2F plans of a
38.0m x 10.2m single-loaded-corridor school block, in millimetres. Each floor has three standard
classrooms (9.0m x 7.5m), a men's and a women's toilet and a dog-leg stair connecting the floors.
The west end of the 1F corridor is the main entrance with a double door (outer door + inner door
enclosing a windbreak vestibule).

Usage (from the repository root):
    .venv\\Scripts\\python.exe examples\\school_building\\draw_school.py [--backend auto|ezdxf|file_ipc] [--out DIR]

Outputs school_plan.dxf and school_plan.png (screenshot) in --out (default: this folder).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]

EXT, INT = 200, 120   # exterior / interior wall thickness (mm)
W, H = 38000, 10200   # building footprint
CORR_Y = 7500         # corridor wall (classrooms y 0..7500, corridor y 7500..10200)
FLOOR_GAP = 17000     # 2F plan is drawn this far above 1F

ROOMS_X = [0, 9000, 18000, 27000, 30600, 34200, W]  # CR1 | CR2 | CR3 | WC-M | WC-F | STAIR
CLASSROOMS = ROOMS_X[:3]

# Openings: "W" window, "D" swing door, "DD" double-leaf door, "O" plain opening.
# Walls on their centrelines: (orientation, fixed coord, start, end, thickness, [(from, to, kind), ...])
# Door swings: (orientation, wall coord, from) -> (hinge side "a"|"b", swing direction +1|-1)
CLASS_DOORS = [(x + 2900, x + 3900, "D") for x in CLASSROOMS] + [(x + 7300, x + 8300, "D") for x in CLASSROOMS]
SOUTH_WINDOWS = ([(x + 900, x + 4300, "W") for x in CLASSROOMS] + [(x + 4700, x + 8100, "W") for x in CLASSROOMS]
                 + [(27900, 29700, "W"), (31500, 33300, "W"), (35000, 37200, "W")])
NORTH_WINDOWS = [(x, x + 1800, "W") for x in range(4000, 34000, 4500)] + [(35200, 37000, "W")]
ENTRANCE = (7930, 9730)  # double-door opening in the corridor-end walls (2 leaves x 900)


def _common_walls(west_openings):
    return [
        ("H", 0,      0, W, EXT, SOUTH_WINDOWS),
        ("H", H,      0, W, EXT, NORTH_WINDOWS),
        ("V", 0,      0, H, EXT, west_openings),
        ("V", W,      0, H, EXT, [(2200, 5200, "W"), (8200, 9500, "W")]),
        ("H", CORR_Y, 0, W, INT, CLASS_DOORS + [(28300, 29200, "D"), (31900, 32800, "D"), (34260, 37900, "O")]),
    ] + [("V", x, 0, CORR_Y, INT, []) for x in ROOMS_X[1:-1]]


DOOR_SWINGS = {("H", CORR_Y, a): ("a", -1) for a, _, k in CLASS_DOORS} | {
    ("H", CORR_Y, 28300): ("b", -1), ("H", CORR_Y, 31900): ("a", -1),
    ("V", 0, ENTRANCE[0]): (None, -1), ("V", 2400, ENTRANCE[0]): (None, -1),  # both leaves swing out (egress)
}


def _classroom_furniture(x0):
    inner = x0 + (EXT if x0 == 0 else INT) / 2
    rects = [
        (inner, 1500, inner + 120, 6000),        # blackboard
        (x0 + 900, 3200, x0 + 1500, 4400),       # teacher's desk
        (x0 + 8400, 600, x0 + 8940, 6000),       # rear lockers
    ]
    rects += [(x0 + 2500 + i * 1000, 1000 + j * 1150, x0 + 2950 + i * 1000, 1600 + j * 1150)
              for i in range(6) for j in range(5)]  # 30 student desks facing the blackboard
    return rects


def _toilet_fixtures():
    rects, circles, ellipses = [], [], []
    for xs in ([27060, 28260, 29460, 30540], [30660, 31820, 32980, 34140]):  # 3 WC stalls per toilet
        for a, b in zip(xs, xs[1:]):
            rects.append((a, 100, b, 1600))
            cx = (a + b) / 2
            rects.append((cx - 250, 100, cx + 250, 300))   # cistern
            ellipses.append((cx, 650, cx, 950, 0.65))     # bowl (major-axis END POINT)
    for y in (3000, 3800, 4600):
        rects.append((27060, y, 27410, y + 400))           # urinals (men)
    for x_counter, ys in ((30000, (3200, 4000, 4800)), (33600, (3200, 4000, 4800))):
        rects.append((x_counter, 2800, x_counter + 540, 5200))  # basin counter
        circles += [(x_counter + 270, y, 180) for y in ys]
    return rects, circles, ellipses


TOILET_RECTS, TOILET_CIRCLES, TOILET_ELLIPSES = _toilet_fixtures()

FLOORS = {
    "1F": {
        # Outer entrance door in the west wall + inner door at x=2400 -> windbreak vestibule
        "walls": _common_walls([(ENTRANCE[0], ENTRANCE[1], "DD")]) + [("V", 2400, CORR_Y, H, INT, [(*ENTRANCE, "DD")])],
        "stair_label": "UP",
        "rooms": [(x + 4500, 350, f"CLASSROOM {i + 1}-1") for i, x in enumerate(CLASSROOMS)]
                 + [(28800, 5600, "WC (M)"), (32400, 5600, "WC (F)"), (36080, 6300, "STAIR"),
                    (20000, 8700, "CORRIDOR"), (850, 8750, "VESTIBULE")],
        "notes": [(-200, 10800, "MAIN ENTRANCE - DOUBLE DOOR (OUTER + INNER, WINDBREAK)", 220)],
        "title": "1F FLOOR PLAN  (38.0m x 10.2m = 387.6m2)  S=1:200",
    },
    "2F": {
        "walls": _common_walls([(8200, 9500, "W")]),
        "stair_label": "DN",
        "rooms": [(x + 4500, 350, f"CLASSROOM {i + 1}-2") for i, x in enumerate(CLASSROOMS)]
                 + [(28800, 5600, "WC (M)"), (32400, 5600, "WC (F)"), (36080, 6300, "STAIR"),
                    (18000, 8700, "CORRIDOR")],
        "notes": [],
        "title": "2F FLOOR PLAN  (38.0m x 10.2m = 387.6m2)  S=1:200",
    },
}

# Dog-leg stair in the stair well x 34200..38000, y 0..7500 (same position on both floors).
# Floor height 3600 = 22 risers x 164; 10 treads x 280 per flight, half landing at the south wall.
STAIR = {"x0": 34300, "x_mid": 36030, "x_mid2": 36130, "x1": 37860, "y_top": 4600, "y_landing": 1800, "treads": 10}

DIMS = [
    (0, 0, W, 0, W / 2, -1400), (0, 0, 0, H, -2600, H / 2),
    *[(a, 0, b, 0, (a + b) / 2, -800) for a, b in zip(ROOMS_X, ROOMS_X[1:])],
    (0, 0, 0, CORR_Y, -1800, CORR_Y / 2), (0, CORR_Y, 0, H, -1800, (CORR_Y + H) / 2),
]

LAYERS = [("A-WALL", "white"), ("A-WIND", "cyan"), ("A-DOOR", "yellow"), ("A-STAIR", "magenta"),
          ("A-FURN", "grey"), ("A-TEXT", "green"), ("A-DIM", "red")]


class Drawer:
    def __init__(self, session: ClientSession):
        self.s = session
        self.oy = 0  # y offset of the floor currently being drawn
        self.calls = 0
        self.errors: list[tuple[str, str | None, str]] = []

    async def call(self, tool: str, **args):
        self.calls += 1
        res = await self.s.call_tool(tool, args)
        text = next((c.text for c in res.content if c.type == "text"), "")
        try:
            body = json.loads(text)
        except ValueError:
            body = {}
        if res.isError or body.get("ok") is False or "error" in body:
            self.errors.append((tool, args.get("operation"), text[:200]))
        return res, body

    async def line(self, x1, y1, x2, y2, layer):
        await self.call("entity", operation="create_line", x1=x1, y1=y1 + self.oy, x2=x2, y2=y2 + self.oy, layer=layer)

    async def rect(self, x1, y1, x2, y2, layer, solid=False):
        _, body = await self.call("entity", operation="create_rectangle",
                                  x1=x1, y1=y1 + self.oy, x2=x2, y2=y2 + self.oy, layer=layer)
        handle = (body.get("payload") or {}).get("handle")
        if solid and handle:
            await self.call("entity", operation="create_hatch", entity_id=handle, data={"pattern": "SOLID"})

    async def arc(self, cx, cy, r, start, end, layer):
        await self.call("entity", operation="create_arc", layer=layer,
                        data={"cx": cx, "cy": cy + self.oy, "radius": r, "start_angle": start, "end_angle": end})

    async def text(self, x, y, text, height, layer, rotation=0.0):
        await self.call("annotation", operation="create_text",
                        data={"x": x, "y": y + self.oy, "text": text, "height": height, "rotation": rotation, "layer": layer})

    # --- building elements ---

    async def walls(self, floor):
        for ori, c, a0, b0, t, openings in floor["walls"]:
            if t == EXT:  # extend exterior walls by half a thickness to close the corners
                start, end = a0 - EXT / 2, b0 + EXT / 2
            else:  # interior walls stop at the inner face of the exterior wall
                start = a0 + EXT / 2 if a0 == 0 else a0
                end = b0 - EXT / 2 if b0 in (W, H) else b0
            cur = start
            for a, b, kind in sorted(openings) + [(end, end, None)]:
                if a > cur:
                    if ori == "H":
                        await self.rect(cur, c - t / 2, a, c + t / 2, "A-WALL", solid=True)
                    else:
                        await self.rect(c - t / 2, cur, c + t / 2, a, "A-WALL", solid=True)
                cur = max(cur, b)
                if kind == "W":
                    await self.window(ori, c, a, b, t)
                elif kind == "D":
                    await self.door(ori, c, a, b, t)
                elif kind == "DD":
                    await self.double_door(c, a, b, t)

    async def window(self, ori, c, a, b, t):
        for off in (-t / 2, 0, t / 2):  # two frame lines + glazing line
            if ori == "H":
                await self.line(a, c + off, b, c + off, "A-WIND")
            else:
                await self.line(c + off, a, c + off, b, "A-WIND")

    async def door(self, ori, c, a, b, t):
        side, sgn = DOOR_SWINGS.get((ori, c, a), ("a", +1))
        width = b - a
        hinge = a if side == "a" else b
        face = c + sgn * t / 2
        if ori == "H":
            await self.line(hinge, face, hinge, face + sgn * width, "A-DOOR")
            if sgn > 0:
                start, end = (0, 90) if side == "a" else (90, 180)
            else:
                start, end = (270, 360) if side == "a" else (180, 270)
            await self.arc(hinge, face, width, start, end, "A-DOOR")
        else:
            await self.line(face, hinge, face + sgn * width, hinge, "A-DOOR")
            if sgn < 0:
                start, end = (90, 180) if side == "a" else (180, 270)
            else:
                start, end = (0, 90) if side == "a" else (270, 360)
            await self.arc(face, hinge, width, start, end, "A-DOOR")

    async def double_door(self, c, a, b, t):
        """Double-leaf door in a vertical wall: hinges at both jambs, leaves meet in the middle."""
        _, sgn = DOOR_SWINGS.get(("V", c, a), (None, -1))
        leaf = (b - a) / 2
        face = c + sgn * t / 2
        for hinge in (a, b):
            await self.line(face, hinge, face + sgn * leaf, hinge, "A-DOOR")
        if sgn < 0:
            await self.arc(face, a, leaf, 90, 180, "A-DOOR")
            await self.arc(face, b, leaf, 180, 270, "A-DOOR")
        else:
            await self.arc(face, a, leaf, 0, 90, "A-DOOR")
            await self.arc(face, b, leaf, 270, 360, "A-DOOR")

    async def stair(self, label):
        s, L = STAIR, "A-STAIR"
        run = (s["y_top"] - s["y_landing"]) / s["treads"]
        for i in range(s["treads"] + 1):
            y = s["y_landing"] + i * run
            await self.line(s["x0"], y, s["x_mid"], y, L)    # west flight (going up, -y)
            await self.line(s["x_mid2"], y, s["x1"], y, L)   # east flight (going up, +y)
        await self.rect(s["x_mid"], s["y_landing"], s["x_mid2"], s["y_top"], L)  # centre stringer
        await self.line(s["x0"], s["y_landing"], s["x1"], s["y_landing"], L)     # landing edge
        xa, xb = (s["x0"] + s["x_mid"]) / 2, (s["x_mid2"] + s["x1"]) / 2
        ymid = (s["y_landing"] + 100) / 2
        await self.call("entity", operation="create_polyline", layer=L,
                        points=[[xa, s["y_top"] - 100 + self.oy], [xa, ymid + self.oy],
                                [xb, ymid + self.oy], [xb, s["y_top"] - 100 + self.oy]])  # walking line
        # arrow head at the top of the east flight
        top = s["y_top"] - 100
        await self.call("entity", operation="create_polyline", layer=L,
                        points=[[xb - 120, top - 250 + self.oy], [xb, top + self.oy], [xb + 120, top - 250 + self.oy]])
        await self.text(xa - 150, s["y_top"] + 150, label, 200, "A-TEXT")
        await self.text(s["x0"] + 100, 700, "LANDING", 160, "A-TEXT")

    async def furniture(self, floor):
        F = "A-FURN"
        rects = [r for x0 in CLASSROOMS for r in _classroom_furniture(x0)] + TOILET_RECTS
        for x1, y1, x2, y2 in rects:
            await self.rect(x1, y1, x2, y2, F)
        for cx, cy, r in TOILET_CIRCLES:
            await self.call("entity", operation="create_circle", layer=F, data={"cx": cx, "cy": cy + self.oy, "radius": r})
        for cx, cy, mx, my, ratio in TOILET_ELLIPSES:
            # create_ellipse: major_x/major_y is the major-axis END POINT (absolute), not a length
            await self.call("entity", operation="create_ellipse", layer=F,
                            data={"cx": cx, "cy": cy + self.oy, "major_x": mx, "major_y": my + self.oy, "ratio": ratio})

    async def annotations(self, floor):
        for x, y, name in floor["rooms"]:
            h = 140 if name == "VESTIBULE" else 220
            await self.text(x - len(name) * h * 0.38, y, name, h, "A-TEXT")
        for x, y, note, h in floor["notes"]:
            await self.text(x, y, note, h, "A-TEXT")
        for x1, y1, x2, y2, dx, dy in DIMS:
            await self.call("annotation", operation="create_dimension_linear",
                            data={"x1": x1, "y1": y1 + self.oy, "x2": x2, "y2": y2 + self.oy,
                                  "dim_x": dx, "dim_y": dy + self.oy})
            # The ezdxf backend renders dimension text at the default 2.5 height (invisible
            # at mm scale), so the value is also written as plain text.
            val = f"{int(abs(x2 - x1) + abs(y2 - y1)):,}"
            if y1 == y2:
                await self.text(dx - len(val) * 70, dy + 80, val, 200, "A-DIM")
            else:
                await self.text(dx - 80, dy - len(val) * 70, val, 200, "A-DIM", rotation=90)
        await self.text(0, -2800, floor["title"], 450, "A-TEXT")

    async def floor(self, name, oy):
        self.oy = oy
        floor = FLOORS[name]
        await self.walls(floor)
        await self.stair(floor["stair_label"])
        await self.furniture(floor)
        await self.annotations(floor)


async def main(backend: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp"],
        env={**os.environ, "AUTOCAD_MCP_BACKEND": backend, "PYTHONPATH": str(REPO_ROOT / "src")},
        cwd=str(REPO_ROOT),
    )
    with open(os.devnull, "w") as server_log:
        async with stdio_client(params, errlog=server_log) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                d = Drawer(session)

                _, status = await d.call("system", operation="status")
                print("backend:", (status.get("payload") or {}).get("backend"))
                await d.call("drawing", operation="create", data={"name": "school_plan"})
                for name, color in LAYERS:
                    await d.call("layer", operation="create", data={"name": name, "color": color})

                await d.floor("1F", 0)
                await d.floor("2F", FLOOR_GAP)
                d.oy = 0
                await d.text(0, -4000, "SCHOOL BUILDING (2 STOREYS, 6 CLASSROOMS, 775.2m2, FL-FL 3,600)", 550, "A-TEXT")

                _, info = await d.call("drawing", operation="info")
                print("entities:", (info.get("payload") or {}).get("entity_count"))
                dxf_path = out_dir / "school_plan.dxf"
                await d.call("drawing", operation="save_as_dxf", data={"path": str(dxf_path)})
                print("saved:", dxf_path)

                await d.call("view", operation="zoom_extents")
                res, _ = await d.call("view", operation="get_screenshot")
                for c in res.content:
                    if c.type == "image":
                        png_path = out_dir / "school_plan.png"
                        png_path.write_bytes(base64.b64decode(c.data))
                        print("screenshot:", png_path)

    print(f"tool calls: {d.calls}, errors: {len(d.errors)}")
    for err in d.errors:
        print("  ", err)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default="auto", choices=["auto", "ezdxf", "file_ipc"])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    asyncio.run(main(args.backend, args.out))
