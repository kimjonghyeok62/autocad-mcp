"""Draw a two-storey single-family house floor plan through the AutoCAD MCP server.

Launches the server over MCP stdio (as an MCP client would) and builds 1F and 2F plans
of a 12.0m x 9.0m house side by side, in millimetres: walls with openings, windows,
door swings, a dog-leg stair, furniture, room labels and dimensions.

Usage (from the repository root):
    .venv\\Scripts\\python.exe examples\\house_plan\\draw_house.py [--backend auto|ezdxf|file_ipc] [--out DIR]

Outputs house_plan.dxf and house_plan.png (screenshot) in --out (default: this folder).
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

EXT, INT = 200, 120  # exterior / interior wall thickness (mm)
FLOOR_GAP = 16000    # 2F plan is drawn this far to the right of 1F

# Openings: "W" window, "D" swing door, "O" plain opening.
# Walls on their centrelines: (orientation, fixed coord, start, end, thickness, [(from, to, kind), ...])
# Door swings: (orientation, wall coord, from) -> (hinge side "a"|"b", swing direction +1|-1)
# Linear dimensions: (x1, y1, x2, y2, dim_line_x, dim_line_y)
FLOORS = {
    "1F": {
        "walls": [
            ("H", 0,     0, 12000, EXT, [(500, 6000, "W"), (7000, 8800, "W"), (10400, 11400, "D")]),
            ("H", 9000,  0, 12000, EXT, [(1200, 3200, "W"), (5200, 5800, "W"), (7200, 8800, "W"), (10200, 11300, "W")]),
            ("V", 0,     0, 9000,  EXT, [(1500, 3500, "W"), (6200, 7800, "W")]),
            ("V", 12000, 0, 9000,  EXT, [(2600, 3400, "W"), (6200, 7800, "W")]),
            ("H", 5000,  0, 12000, INT, [(3500, 4400, "D"), (5100, 5900, "D"), (6700, 7600, "D")]),
            ("V", 4500,  5000, 9000, INT, []),
            ("V", 6500,  5000, 9000, INT, []),
            ("V", 10000, 0, 5000,  INT, [(600, 1600, "D")]),
            ("H", 2000,  10000, 12000, INT, [(10060, 10980, "O")]),
        ],
        "doors": {
            ("H", 0, 10400): ("b", +1), ("H", 5000, 3500): ("b", +1), ("H", 5000, 5100): ("a", +1),
            ("H", 5000, 6700): ("a", +1), ("V", 10000, 600): ("a", -1),
        },
        "rooms": [
            (2250, 7300, "MASTER BED"), (5500, 7600, "BATH"), (9250, 7300, "BED 2"),
            (3000, 1300, "LIVING"), (8200, 3300, "KITCHEN/DINING"), (11000, 900, "ENTRY"),
        ],
        "rects": [
            (1500, 6700, 3100, 8800), (1500, 8300, 3100, 8800),      # queen bed
            (400, 5200, 1000, 7000),                                 # wardrobe
            (8100, 6700, 9200, 8800), (8100, 8400, 9200, 8800),      # bed
            (4700, 7600, 6300, 8800),                                # bathtub
            (5800, 6250, 6300, 6450),                                # cistern
            (1200, 2700, 4200, 3600), (1200, 2700, 1500, 3600), (3900, 2700, 4200, 3600),  # sofa
            (1900, 1500, 3500, 2200),                                # coffee table
            (300, 4500, 3300, 4850),                                 # TV cabinet
            (6700, 200, 9800, 800),                                  # kitchen counter
            (7300, 2400, 9100, 3300),                                # dining table
            (7475, 1825, 7925, 2275), (8475, 1825, 8925, 2275),      # chairs
            (7475, 3425, 7925, 3875), (8475, 3425, 8925, 3875),
            (11500, 1000, 11900, 1900),                              # shoe cabinet
        ],
        "circles": [(4900, 6600, 200), (7400, 500, 180)],            # basin, sink
        # Beams in the ceiling (drawn dashed): (x1, y1, x2, y2, width, label).
        # B1 carries the 2F BED 3 / FAMILY ROOM wall (x=3500) over the open living room.
        "beams": [(3500, 100, 3500, 4940, 250, "B1 250x500")],
        "ellipses": [(6000, 6700, 6000, 7000, 0.65)],                # toilet (major-axis END POINT, absolute)
        "stair_label": "UP",
        "dims": [
            (0, 0, 12000, 0, 6000, -1400), (0, 0, 0, 9000, -1400, 4500),
            (0, 0, 6500, 0, 3000, -800), (6500, 0, 10000, 0, 8000, -800), (10000, 0, 12000, 0, 11000, -800),
            (0, 0, 0, 5000, -800, 2500), (0, 5000, 0, 9000, -800, 7000),
            (0, 9000, 4500, 9000, 2000, 9800), (4500, 9000, 6500, 9000, 5500, 9800), (6500, 9000, 12000, 9000, 9250, 9800),
        ],
        "title": "1F FLOOR PLAN  (12.0m x 9.0m = 108m2)  S=1:100",
    },
    "2F": {
        "walls": [
            ("H", 0,     0, 12000, EXT, [(800, 3200, "W"), (4800, 9200, "W"), (10400, 11600, "W")]),
            ("H", 9000,  0, 12000, EXT, [(1200, 3200, "W"), (5200, 5800, "W"), (8000, 11000, "W")]),
            ("V", 0,     0, 9000,  EXT, [(1500, 3500, "W"), (6200, 7800, "W")]),
            ("V", 12000, 0, 9000,  EXT, [(3000, 4000, "W"), (6200, 7800, "W")]),
            ("H", 5000,  0, 12000, INT, [(3600, 4400, "D"), (5100, 5900, "D"), (7400, 8300, "D")]),
            ("V", 4500,  5000, 9000, INT, []),
            ("V", 6500,  5000, 9000, INT, []),
            ("V", 3500,  0, 5000,  INT, [(3800, 4700, "D")]),
            ("V", 10000, 2000, 5000, INT, []),
        ],
        "doors": {
            ("H", 5000, 3600): ("a", +1), ("H", 5000, 5100): ("a", +1), ("H", 5000, 7400): ("a", +1),
            ("V", 3500, 3800): ("b", -1),
        },
        "rooms": [
            (1750, 2600, "BED 3"), (2250, 7300, "BED 4"), (5500, 7600, "BATH"), (9250, 7300, "STUDY"),
            (6800, 2600, "FAMILY ROOM"), (11000, 900, "HALL"),
        ],
        "rects": [
            (500, 1400, 2100, 3400), (500, 1400, 900, 3400),         # bed 3
            (2600, 200, 3300, 1800),                                 # wardrobe
            (1500, 6800, 3100, 8800), (1500, 8300, 3100, 8800),      # bed 4
            (4700, 7600, 6300, 8800),                                # bathtub (stacked on 1F)
            (5800, 6250, 6300, 6450),                                # cistern
            (8200, 8200, 10200, 8800), (8950, 7600, 9450, 8100),     # desk, chair
            (7100, 5600, 7500, 8000),                                # bookshelf
            (5200, 3700, 8200, 4600),                                # sofa
            (5800, 2300, 7400, 3000),                                # coffee table
            (5500, 200, 7500, 500),                                  # TV cabinet
        ],
        "circles": [(4900, 6600, 200)],                              # basin
        "ellipses": [(6000, 6700, 6000, 7000, 0.65)],                # toilet
        "stair_label": "DN",
        "rails": [(10000, 2000, 11000, 2000)],                       # balustrade over the lower flight
        "dims": [
            (0, 0, 12000, 0, 6000, -1400), (0, 0, 0, 9000, -1400, 4500),
            (0, 0, 3500, 0, 1750, -800), (3500, 0, 10000, 0, 6750, -800), (10000, 0, 12000, 0, 11000, -800),
            (0, 0, 0, 5000, -800, 2500), (0, 5000, 0, 9000, -800, 7000),
            (0, 9000, 4500, 9000, 2250, 9800), (4500, 9000, 6500, 9000, 5500, 9800), (6500, 9000, 12000, 9000, 9250, 9800),
        ],
        "title": "2F FLOOR PLAN  (12.0m x 9.0m = 108m2)  S=1:100",
    },
}

# Dog-leg stair in the stair well x 10000..12000, y 2000..5000 (same position on both floors).
STAIR = {"x0": 10060, "x_mid": 10980, "x_mid2": 11020, "x1": 11900, "y0": 2060, "y_landing": 4100, "treads": 8}

LAYERS = [("A-WALL", "white"), ("A-WIND", "cyan"), ("A-DOOR", "yellow"), ("A-STAIR", "magenta"), ("A-BEAM", 30),
          ("A-FURN", "grey"), ("A-TEXT", "green"), ("A-DIM", "red")]


class Drawer:
    def __init__(self, session: ClientSession):
        self.s = session
        self.ox = 0  # x offset of the floor currently being drawn
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
        await self.call("entity", operation="create_line", x1=x1 + self.ox, y1=y1, x2=x2 + self.ox, y2=y2, layer=layer)

    async def rect(self, x1, y1, x2, y2, layer, solid=False):
        _, body = await self.call("entity", operation="create_rectangle",
                                  x1=x1 + self.ox, y1=y1, x2=x2 + self.ox, y2=y2, layer=layer)
        handle = (body.get("payload") or {}).get("handle")
        if solid and handle:
            await self.call("entity", operation="create_hatch", entity_id=handle, data={"pattern": "SOLID"})

    async def arc(self, cx, cy, r, start, end, layer):
        await self.call("entity", operation="create_arc", layer=layer,
                        data={"cx": cx + self.ox, "cy": cy, "radius": r, "start_angle": start, "end_angle": end})

    async def text(self, x, y, text, height, layer, rotation=0.0):
        await self.call("annotation", operation="create_text",
                        data={"x": x + self.ox, "y": y, "text": text, "height": height, "rotation": rotation, "layer": layer})

    # --- building elements ---

    async def walls(self, floor):
        for ori, c, a0, b0, t, openings in floor["walls"]:
            if t == EXT:  # extend exterior walls by half a thickness to close the corners
                start, end = a0 - EXT / 2, b0 + EXT / 2
            else:  # interior walls start at the inner face of the exterior wall
                start, end = (a0 + EXT / 2 if a0 == 0 else a0), b0
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
                    await self.door(ori, c, a, b, t, floor["doors"])

    async def window(self, ori, c, a, b, t):
        for off in (-t / 2, 0, t / 2):  # two frame lines + glazing line
            if ori == "H":
                await self.line(a, c + off, b, c + off, "A-WIND")
            else:
                await self.line(c + off, a, c + off, b, "A-WIND")

    async def door(self, ori, c, a, b, t, swings):
        side, sgn = swings.get((ori, c, a), ("a", +1))
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

    async def stair(self, label):
        s, L = STAIR, "A-STAIR"
        run = (s["y_landing"] - s["y0"]) / s["treads"]
        for i in range(s["treads"] + 1):
            y = s["y0"] + i * run
            await self.line(s["x0"], y, s["x_mid"], y, L)    # lower flight (going up, +y)
            await self.line(s["x_mid2"], y, s["x1"], y, L)   # upper flight (going up, -y)
        await self.rect(s["x_mid"], s["y0"], s["x_mid2"], s["y_landing"], L)  # centre stringer
        xa, xb = (s["x0"] + s["x_mid"]) / 2, (s["x_mid2"] + s["x1"]) / 2
        ytop = (s["y_landing"] + 4940) / 2
        await self.call("entity", operation="create_polyline", layer=L,
                        points=[[xa + self.ox, s["y0"] + 150], [xa + self.ox, ytop],
                                [xb + self.ox, ytop], [xb + self.ox, s["y0"] + 150]])  # walking line
        await self.text(xa - 150, s["y0"] - 300, label, 180, "A-TEXT")

    async def beams(self, floor, dash=400, gap=200):
        for x1, y1, x2, y2, width, label in floor.get("beams", []):
            vertical = x1 == x2
            length = abs((y2 - y1) if vertical else (x2 - x1))
            for off in (-width / 2, width / 2):  # both beam edges as dashed lines
                pos = 0
                while pos < length:
                    seg_end = min(pos + dash, length)
                    if vertical:
                        await self.line(x1 + off, y1 + pos, x1 + off, y1 + seg_end, "A-BEAM")
                    else:
                        await self.line(x1 + pos, y1 + off, x1 + seg_end, y1 + off, "A-BEAM")
                    pos += dash + gap
            if vertical:
                await self.text(x1 + width / 2 + 80, (y1 + y2) / 2 - 1200, f"{label} (ABOVE)", 150, "A-BEAM", rotation=90)
            else:
                await self.text((x1 + x2) / 2 - 600, y1 + width / 2 + 80, f"{label} (ABOVE)", 150, "A-BEAM")

    async def furniture(self, floor):
        F = "A-FURN"
        for x1, y1, x2, y2 in floor["rects"]:
            await self.rect(x1, y1, x2, y2, F)
        for cx, cy, r in floor["circles"]:
            await self.call("entity", operation="create_circle", layer=F, data={"cx": cx + self.ox, "cy": cy, "radius": r})
        for cx, cy, mx, my, ratio in floor["ellipses"]:
            # create_ellipse: major_x/major_y is the major-axis END POINT (absolute), not a length
            await self.call("entity", operation="create_ellipse", layer=F,
                            data={"cx": cx + self.ox, "cy": cy, "major_x": mx + self.ox, "major_y": my, "ratio": ratio})
        for x1, y1, x2, y2 in floor.get("rails", []):
            await self.line(x1, y1, x2, y2, "A-STAIR")

    async def annotations(self, floor):
        for x, y, name in floor["rooms"]:
            await self.text(x - len(name) * 75, y, name, 200, "A-TEXT")
        for x1, y1, x2, y2, dx, dy in floor["dims"]:
            await self.call("annotation", operation="create_dimension_linear",
                            data={"x1": x1 + self.ox, "y1": y1, "x2": x2 + self.ox, "y2": y2,
                                  "dim_x": dx + self.ox, "dim_y": dy})
            # The ezdxf backend renders dimension text at the default 2.5 height (invisible
            # at mm scale), so the value is also written as plain text.
            val = str(int(abs(x2 - x1) + abs(y2 - y1)))
            if y1 == y2:
                await self.text(dx - len(val) * 60, dy + 60, val, 160, "A-DIM")
            else:
                await self.text(dx - 60, dy - len(val) * 60, val, 160, "A-DIM", rotation=90)
        await self.text(0, -2600, floor["title"], 350, "A-TEXT")

    async def floor(self, name, ox):
        self.ox = ox
        floor = FLOORS[name]
        await self.walls(floor)
        await self.stair(floor["stair_label"])
        await self.furniture(floor)
        await self.beams(floor)
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
                await d.call("drawing", operation="create", data={"name": "house_plan"})
                for name, color in LAYERS:
                    await d.call("layer", operation="create", data={"name": name, "color": color})

                await d.floor("1F", 0)
                await d.floor("2F", FLOOR_GAP)
                d.ox = 0
                await d.text(0, -3500, "SINGLE-FAMILY HOUSE (2 STOREYS, 216m2, 4BR/2BA + STUDY)", 450, "A-TEXT")

                _, info = await d.call("drawing", operation="info")
                print("entities:", (info.get("payload") or {}).get("entity_count"))
                dxf_path = out_dir / "house_plan.dxf"
                await d.call("drawing", operation="save_as_dxf", data={"path": str(dxf_path)})
                print("saved:", dxf_path)

                await d.call("view", operation="zoom_extents")
                res, _ = await d.call("view", operation="get_screenshot")
                for c in res.content:
                    if c.type == "image":
                        png_path = out_dir / "house_plan.png"
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
