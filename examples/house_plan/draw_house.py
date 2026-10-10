"""Draw a single-family house floor plan through the AutoCAD MCP server.

Launches the server over MCP stdio (as an MCP client would) and builds a
12.0m x 9.0m, 2-bedroom / 2-bath ground floor plan in millimetres:
walls with openings, windows, door swings, furniture, room labels, dimensions.

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

# Axis-aligned walls on their centrelines:
# (orientation, fixed coord, start, end, thickness, [(open_from, open_to, "W"indow | "D"oor), ...])
WALLS = [
    ("H", 0,     0, 12000, EXT, [(1000, 5000, "W"), (7000, 8800, "W"), (10400, 11400, "D")]),
    ("H", 9000,  0, 12000, EXT, [(1200, 3200, "W"), (5200, 5800, "W"), (7200, 8800, "W"), (10200, 11300, "W")]),
    ("V", 0,     0, 9000,  EXT, [(1500, 3500, "W"), (6200, 7800, "W")]),
    ("V", 12000, 0, 9000,  EXT, [(2600, 3400, "W"), (6200, 7800, "W")]),
    ("H", 5000,  0, 12000, INT, [(3500, 4400, "D"), (5100, 5900, "D"), (6700, 7600, "D")]),
    ("V", 4500,  5000, 9000, INT, []),
    ("V", 6500,  5000, 9000, INT, []),
    ("V", 10000, 0, 3800,  INT, [(600, 1600, "D")]),
    ("H", 2000,  10000, 12000, INT, []),
    ("H", 3800,  10000, 12000, INT, [(10400, 11200, "D")]),
]

# Door swing per opening (orientation, wall coord, open_from) -> (hinge side "a"|"b", swing direction +1|-1)
DOOR_SWING = {
    ("H", 0, 10400): ("b", +1), ("H", 5000, 3500): ("b", +1), ("H", 5000, 5100): ("a", +1),
    ("H", 5000, 6700): ("a", +1), ("V", 10000, 600): ("a", -1),
    ("H", 3800, 10400): ("a", -1),
}

ROOMS = [
    (2250, 7300, "MASTER BED"), (5500, 7600, "BATH"), (9250, 7300, "BED 2"),
    (3000, 2400, "LIVING"), (8200, 3300, "KITCHEN/DINING"), (11000, 900, "ENTRY"), (11000, 2800, "BATH 2"),
]

# Linear dimensions: (x1, y1, x2, y2, dim_line_x, dim_line_y)
DIMS = [
    (0, 0, 12000, 0, 6000, -1400), (0, 0, 0, 9000, -1400, 4500),
    (0, 0, 6500, 0, 3000, -800), (6500, 0, 10000, 0, 8000, -800), (10000, 0, 12000, 0, 11000, -800),
    (0, 0, 0, 5000, -800, 2500), (0, 5000, 0, 9000, -800, 7000),
    (0, 9000, 4500, 9000, 2000, 9800), (4500, 9000, 6500, 9000, 5500, 9800),
    (6500, 9000, 12000, 9000, 9250, 9800),
]

LAYERS = [("A-WALL", "white"), ("A-WIND", "cyan"), ("A-DOOR", "yellow"),
          ("A-FURN", "grey"), ("A-TEXT", "green"), ("A-DIM", "red")]


class Drawer:
    def __init__(self, session: ClientSession):
        self.s = session
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
        await self.call("entity", operation="create_line", x1=x1, y1=y1, x2=x2, y2=y2, layer=layer)

    async def rect(self, x1, y1, x2, y2, layer, solid=False):
        _, body = await self.call("entity", operation="create_rectangle", x1=x1, y1=y1, x2=x2, y2=y2, layer=layer)
        handle = (body.get("payload") or {}).get("handle")
        if solid and handle:
            await self.call("entity", operation="create_hatch", entity_id=handle, data={"pattern": "SOLID"})

    async def arc(self, cx, cy, r, start, end, layer):
        await self.call("entity", operation="create_arc", layer=layer,
                        data={"cx": cx, "cy": cy, "radius": r, "start_angle": start, "end_angle": end})

    async def text(self, x, y, text, height, layer, rotation=0.0):
        await self.call("annotation", operation="create_text",
                        data={"x": x, "y": y, "text": text, "height": height, "rotation": rotation, "layer": layer})

    # --- building elements ---

    async def walls(self):
        for ori, c, a0, b0, t, openings in WALLS:
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
                    await self.door(ori, c, a, b, t)

    async def window(self, ori, c, a, b, t):
        for off in (-t / 2, 0, t / 2):  # two frame lines + glazing line
            if ori == "H":
                await self.line(a, c + off, b, c + off, "A-WIND")
            else:
                await self.line(c + off, a, c + off, b, "A-WIND")

    async def door(self, ori, c, a, b, t):
        side, sgn = DOOR_SWING.get((ori, c, a), ("a", +1))
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

    async def furniture(self):
        F = "A-FURN"
        await self.rect(1500, 6700, 3100, 8800, F); await self.rect(1500, 8300, 3100, 8800, F)      # queen bed
        await self.rect(400, 5200, 1000, 7000, F)                                                   # wardrobe
        await self.rect(8100, 6700, 9200, 8800, F); await self.rect(8100, 8400, 9200, 8800, F)      # bed 2
        await self.rect(4700, 7600, 6300, 8800, F)                                                  # bathtub
        # create_ellipse: major_x/major_y is the major-axis END POINT (absolute), not a length
        await self.call("entity", operation="create_ellipse", layer=F,
                        data={"cx": 6000, "cy": 6700, "major_x": 6000, "major_y": 7000, "ratio": 0.65})  # toilet
        await self.rect(5800, 6250, 6300, 6450, F)                                                  # cistern
        await self.call("entity", operation="create_circle", layer=F, data={"cx": 4900, "cy": 6600, "radius": 200})  # basin
        await self.rect(1200, 3900, 4200, 4800, F)                                                  # sofa
        await self.rect(1200, 3900, 1500, 4800, F); await self.rect(3900, 3900, 4200, 4800, F)
        await self.rect(1900, 2600, 3500, 3400, F)                                                  # coffee table
        await self.rect(1500, 200, 4500, 600, F)                                                    # TV cabinet
        await self.rect(6700, 200, 9800, 800, F)                                                    # kitchen counter
        await self.call("entity", operation="create_circle", layer=F, data={"cx": 7400, "cy": 500, "radius": 180})  # sink
        await self.rect(7300, 2400, 9100, 3300, F)                                                  # dining table
        for cx in (7700, 8700):
            for cy in (2050, 3650):
                await self.rect(cx - 225, cy - 225, cx + 225, cy + 225, F)                          # chairs
        await self.call("entity", operation="create_ellipse", layer=F,
                        data={"cx": 11600, "cy": 2900, "major_x": 11900, "major_y": 2900, "ratio": 0.65})  # toilet 2
        await self.rect(10200, 2200, 10700, 2700, F)                                                # basin 2
        await self.rect(10100, 1000, 10500, 1900, F)                                                # shoe cabinet

    async def annotations(self):
        for x, y, name in ROOMS:
            await self.text(x - len(name) * 75, y, name, 200, "A-TEXT")
        for x1, y1, x2, y2, dx, dy in DIMS:
            await self.call("annotation", operation="create_dimension_linear",
                            data={"x1": x1, "y1": y1, "x2": x2, "y2": y2, "dim_x": dx, "dim_y": dy})
            # The ezdxf backend renders dimension text at the default 2.5 height (invisible
            # at mm scale), so the value is also written as plain text.
            val = str(int(abs(x2 - x1) + abs(y2 - y1)))
            if y1 == y2:
                await self.text(dx - len(val) * 60, dy + 60, val, 160, "A-DIM")
            else:
                await self.text(dx - 60, dy - len(val) * 60, val, 160, "A-DIM", rotation=90)
        await self.text(0, -2600, "SINGLE-FAMILY HOUSE - 1F FLOOR PLAN  (12.0m x 9.0m = 108m2, 2BR/2BA)  S=1:100",
                        350, "A-TEXT")


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

                await d.walls()
                await d.furniture()
                await d.annotations()

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
