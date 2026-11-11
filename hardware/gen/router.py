"""
router.py - small grid maze router used by gen_pcb.py.

KiCad has no built-in autorouter, so the board is routed here: an A* search on
a 0.05 mm grid over two signal layers (front and back) with through vias,
8 directions (45 degree steps) and penalties for bends and vias. Clearance is
enforced by "inflating" every other-net copper shape by track half-width +
clearance before the search, so any path through free cells is legal; the
result is checked again afterwards by KiCad's own DRC.

Pure Python, no dependencies. All coordinates are millimetres.
"""
import heapq
import math
from collections import defaultdict

PITCH = 0.05
DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
COST_ORTH, COST_DIAG = 10, 14
COST_TURN45, COST_TURN90 = 25, 80
COST_VIA = 350
NO_DIR = 8
_NONZERO = bytes([0] + [1] * 255)
_INVERT = bytes([1] + [0] * 255)


def _cell(v):
    return int(round(v / PITCH))


class Shape:
    """Convex copper shape: rectangle, circle or thick segment."""

    def __init__(self, kind, a, b=None, r=0.0):
        self.kind, self.a, self.b, self.r = kind, a, b, r

    @staticmethod
    def rect(x0, y0, x1, y1):
        return Shape("rect", (min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1)))

    @staticmethod
    def circle(x, y, r):
        return Shape("circle", (x, y), None, r)

    @staticmethod
    def segment(x0, y0, x1, y1, width):
        return Shape("seg", (x0, y0), (x1, y1), width / 2.0)

    def spans(self, grow):
        """Yield (row, col_first, col_last) of the grid cells covered after growing the shape by `grow` mm."""
        rows = {}

        def add(y, xa, xb):
            lo, hi = rows.get(y, (1e9, -1e9))
            rows[y] = (min(lo, xa), max(hi, xb))

        def disc(cx, cy, rad):
            for y in range(int(math.ceil((cy - rad) / PITCH)), int(math.floor((cy + rad) / PITCH)) + 1):
                dy = y * PITCH - cy
                dx = math.sqrt(max(rad * rad - dy * dy, 0.0))
                add(y, cx - dx, cx + dx)

        def poly(pts):
            n = len(pts)
            for i in range(n):
                (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
                if ya > yb:
                    xa, ya, xb, yb = xb, yb, xa, ya
                for y in range(int(math.ceil(ya / PITCH - 1e-9)), int(math.floor(yb / PITCH + 1e-9)) + 1):
                    yy = y * PITCH
                    if yb - ya < 1e-9:
                        add(y, min(xa, xb), max(xa, xb))
                    else:
                        t = min(max((yy - ya) / (yb - ya), 0.0), 1.0)
                        x = xa + t * (xb - xa)
                        add(y, x, x)

        if self.kind == "rect":
            (x0, y0), (x1, y1) = self.a, self.b
            for y in range(int(math.ceil((y0 - grow) / PITCH - 1e-9)), int(math.floor((y1 + grow) / PITCH + 1e-9)) + 1):
                add(y, x0 - grow, x1 + grow)
        elif self.kind == "circle":
            disc(self.a[0], self.a[1], self.r + grow)
        else:
            (x0, y0), (x1, y1) = self.a, self.b
            rad = self.r + grow
            disc(x0, y0, rad)
            disc(x1, y1, rad)
            length = math.hypot(x1 - x0, y1 - y0)
            if length > 1e-9:
                nx, ny = -(y1 - y0) / length * rad, (x1 - x0) / length * rad
                poly([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
        for y, (xa, xb) in rows.items():
            yield y, int(math.ceil(xa / PITCH - 1e-9)), int(math.floor(xb / PITCH + 1e-9))

    def cells(self, shrink=0.0):
        """Grid cells inside the shape (shrunk by `shrink` mm)."""
        return [(x, y) for y, xa, xb in self.spans(-shrink) for x in range(xa, xb + 1)]


class Router:
    def __init__(self, width, height, classes, n_layers=2):
        """classes: name -> grow radius in mm (track half-width + clearance + margin)."""
        self.w, self.h = width, height
        self.nx, self.ny = _cell(width) + 1, _cell(height) + 1
        self.size = self.nx * self.ny
        self.classes = dict(classes)
        self.n_layers = n_layers
        self.cnt = {(l, c): bytearray(self.size) for l in range(n_layers) for c in classes}
        self.hard = {(l, c): bytearray(self.size) for l in range(n_layers) for c in classes}
        self.novia = bytearray(self.size)
        self.items = defaultdict(list)    # net -> [(layers, shape)]
        self.anchor = defaultdict(set)    # net -> {(layer, x, y)}: cells a new track may end on
        self.cost_mult = [bytearray(b"\x0a" * self.size) for _ in range(n_layers)]  # x10 fixed point

    # -- obstacle bookkeeping ------------------------------------------------
    def _paint(self, arr, shape, grow, delta):
        nx, ny = self.nx, self.ny
        for y, xa, xb in shape.spans(grow):
            if 0 <= y < ny:
                base = y * nx
                for x in range(max(xa, 0), min(xb, nx - 1) + 1):
                    v = arr[base + x] + delta
                    arr[base + x] = 255 if v > 255 else (0 if v < 0 else v)

    def add(self, net, layers, shape, anchor=False):
        """Add copper. net=None means it blocks every net (hole, keep-out)."""
        for l in layers:
            for c, grow in self.classes.items():
                self._paint(self.cnt[(l, c)], shape, grow, +1)
        if net is not None:
            self.items[net].append((tuple(layers), shape))
            if anchor:
                for l in layers:
                    for x, y in shape.cells(0.02):
                        self.anchor[net].add((l, x, y))

    def remove_net_routes(self, net, routes):
        """Undo add() for the tracks/vias of a net (rip-up)."""
        for layers, shape in routes:
            for l in layers:
                for c, grow in self.classes.items():
                    self._paint(self.cnt[(l, c)], shape, grow, -1)
            self.items[net].remove((tuple(layers), shape))

    def block(self, layers, shape, grow=0.0, classes=None):
        """Area closed to every net on these layers (board edge margin, keep-out)."""
        for l in layers:
            for c in (classes or self.classes):
                self._paint(self.hard[(l, c)], shape, grow, +1)

    def block_vias(self, shape, grow=0.0):
        self._paint(self.novia, shape, grow, +1)

    def set_cost(self, layer, shape, mult):
        arr = self.cost_mult[layer]
        for y, xa, xb in shape.spans(0.0):
            if 0 <= y < self.ny:
                for x in range(max(xa, 0), min(xb, self.nx - 1) + 1):
                    arr[y * self.nx + x] = int(mult * 10)

    def _blocked(self, net, cls):
        """Per layer: bytearray with 1 where a track/via of class `cls` of `net` may not have its centre."""
        nx, ny, size = self.nx, self.ny, self.size
        grow = self.classes[cls]
        out = []
        for l in range(self.n_layers):
            a, hd = self.cnt[(l, cls)], self.hard[(l, cls)]
            merged = int.from_bytes(a.translate(_NONZERO), "little") | int.from_bytes(hd.translate(_NONZERO), "little")
            blk = bytearray(merged.to_bytes(size, "little"))
            own = {}
            for layers, shape in self.items.get(net, ()):
                if l in layers:
                    for y, xa, xb in shape.spans(grow):
                        if 0 <= y < ny:
                            base = y * nx
                            for x in range(max(xa, 0), min(xb, nx - 1) + 1):
                                own[base + x] = own.get(base + x, 0) + 1
            for i, k in own.items():        # cells covered only by the net's own copper are free
                blk[i] = 1 if (a[i] > k or hd[i]) else 0
            out.append(blk)
        return out

    # -- search ----------------------------------------------------------------
    def find_path(self, net, sources, targets, cls, via_cls="via", allow_via=True, stop_at_via_site=False,
                  layers_allowed=None, max_nodes=1500000, max_cost=None):
        """
        A* from the `sources` cells to any of the `targets` cells.
        sources / targets: iterables of (layer, x, y).
        stop_at_via_site: also accept any cell where a via fits (used to reach a plane).
        Returns a list of (layer, x, y) or None.
        """
        nx, ny, size = self.nx, self.ny, self.size
        free = [bytes(b.translate(_INVERT)) for b in self._blocked(net, cls)]
        via_free = None
        if allow_via or stop_at_via_site:
            merged = int.from_bytes(self.novia.translate(_NONZERO), "little")
            for b in self._blocked(net, via_cls):
                merged |= int.from_bytes(b, "little")
            via_free = merged.to_bytes(size, "little").translate(_INVERT)

        tgt = set((l, y * nx + x) for l, x, y in targets)
        if not tgt and not stop_at_via_site:
            return None
        if tgt:
            txs = [c % nx for _, c in tgt]
            tys = [c // nx for _, c in tgt]
            bx0, bx1, by0, by1 = min(txs), max(txs), min(tys), max(tys)
        layers_ok = set(layers_allowed) if layers_allowed is not None else set(range(self.n_layers))

        def heur(x, y):
            if not tgt or stop_at_via_site:
                return 0
            dx = bx0 - x if x < bx0 else (x - bx1 if x > bx1 else 0)
            dy = by0 - y if y < by0 else (y - by1 if y > by1 else 0)
            return COST_ORTH * max(dx, dy) + (COST_DIAG - COST_ORTH) * min(dx, dy)

        heap, best, parent = [], {}, {}
        for l, x, y in sources:
            if not (0 <= x < nx and 0 <= y < ny) or l not in layers_ok:
                continue
            c = y * nx + x
            if not free[l][c]:
                continue
            s = (l, c, NO_DIR)
            best[s] = 0
            heapq.heappush(heap, (heur(x, y), 0, s))
        mult = self.cost_mult
        goal, nodes = None, 0
        while heap:
            f, g, s = heapq.heappop(heap)
            if best.get(s, 1 << 60) < g:
                continue
            l, c, d = s
            if max_cost is not None and g > max_cost:
                break
            if (l, c) in tgt or (stop_at_via_site and via_free[c] and g > 0):
                goal = s
                break
            nodes += 1
            if nodes > max_nodes:
                break
            x, y = c % nx, c // nx
            fl = free[l]
            for nd in range(8):
                if d != NO_DIR:
                    turn = (nd - d) % 8
                    turn = min(turn, 8 - turn)
                    if turn > 2:
                        continue
                else:
                    turn = 0
                dx, dy = DIRS[nd]
                x2, y2 = x + dx, y + dy
                if not (0 <= x2 < nx and 0 <= y2 < ny):
                    continue
                c2 = y2 * nx + x2
                if not fl[c2]:
                    continue
                if dx and dy and not (fl[c + dx] and fl[c + dy * nx]):
                    continue    # do not cut the corner of an obstacle
                step = (COST_DIAG if (dx and dy) else COST_ORTH) * mult[l][c2] // 10
                g2 = g + step + (COST_TURN45 if turn == 1 else (COST_TURN90 if turn == 2 else 0))
                s2 = (l, c2, nd)
                if g2 < best.get(s2, 1 << 60):
                    best[s2] = g2
                    parent[s2] = s
                    heapq.heappush(heap, (g2 + heur(x2, y2), g2, s2))
            if allow_via and via_free[c]:
                for l2 in layers_ok:
                    if l2 != l and free[l2][c]:
                        s2 = (l2, c, NO_DIR)
                        g2 = g + COST_VIA
                        if g2 < best.get(s2, 1 << 60):
                            best[s2] = g2
                            parent[s2] = s
                            heapq.heappush(heap, (g2 + heur(x, y), g2, s2))
        if goal is None:
            return None
        path = []
        s = goal
        while s is not None:
            path.append((s[0], s[1] % nx, s[1] // nx))
            s = parent.get(s)
        path.reverse()
        return path

    @staticmethod
    def path_to_tracks(path):
        """Collapse a cell path into straight segments and via positions (mm)."""
        segs, vias = [], []
        start = path[0]
        prev = path[0]
        direction = None
        for cur in path[1:]:
            if cur[0] != prev[0]:
                if start[1:] != prev[1:]:
                    segs.append((start[0], start[1] * PITCH, start[2] * PITCH, prev[1] * PITCH, prev[2] * PITCH))
                vias.append((prev[1] * PITCH, prev[2] * PITCH))
                start, direction = cur, None
            else:
                d = (cur[1] - prev[1], cur[2] - prev[2])
                if direction is not None and d != direction:
                    segs.append((start[0], start[1] * PITCH, start[2] * PITCH, prev[1] * PITCH, prev[2] * PITCH))
                    start = prev
                direction = d
            prev = cur
        if start[1:] != prev[1:]:
            segs.append((start[0], start[1] * PITCH, start[2] * PITCH, prev[1] * PITCH, prev[2] * PITCH))
        return segs, vias
