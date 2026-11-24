"""Finding a way round things, from the room's collision shapes.

The game keeps each room's floors, stairs and walls as triangles. Floors and
stairs are laid over a grid of small squares, remembering the height of the
ground in each square (a square can have several: a balcony over a hall).
Squares too near a wall that stands at body height are blocked. The shortest
way through what is left is then found, going up and down stairs where the
ground rises a little from one square to the next.
"""
import heapq
import math

CELL = 150.0        # size of a grid square, in world units (millimetres)
CLEARANCE = 330.0   # how far from a wall the player's middle has to stay
COMFORT = 650.0     # nearer a wall than this is allowed, but the way prefers not to be
NEAR_COST = 2.5     # how much longer a step near a wall counts
STEP = 320.0        # how much the ground may rise between neighbouring squares (steep stairs too)
RISER = 260.0       # a wall no taller than this is a stair step's front (stepped up, not walked round)
HEADROOM = (400.0, 1800.0)   # ground with other ground this much over it is under a staircase: no room to stand
HEAD_LOG = False
HEADROOM_ON = False     # set per room (roomlist.LOW_HEAD_ROOMS): tried everywhere, it cut the Lecture Room's back row off.
                        # "sloped": only under a sloping staircase; "any": under any ground (stairs made of flat steps)
BODY = (250.0, 1000.0)   # the part of the body walls are checked against, above the ground


def _crosses(ax, az, bx, bz, cx, cz, dx, dz):
    """True if segment a-b crosses segment c-d."""
    def side(px, pz, qx, qz, rx, rz):
        return (qx - px) * (rz - pz) - (qz - pz) * (rx - px)
    d1 = side(cx, cz, dx, dz, ax, az)
    d2 = side(cx, cz, dx, dz, bx, bz)
    d3 = side(ax, az, bx, bz, cx, cz)
    d4 = side(ax, az, bx, bz, dx, dz)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


class NavMesh:
    def __init__(self, triangles, margin=300.0, clearance=CLEARANCE, up_only=False, ceilings=False):
        """triangles: [((x, y, z), (x, y, z), (x, y, z))]. clearance: how far from a wall the
        player's middle keeps (a tighter mesh squeezes through narrow gaps). up_only: flat
        triangles facing down are ceilings, not ground (left out)."""
        self.clearance = clearance
        floors, walls, downs = [], [], []
        for a, b, c in triangles:
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length < 1e-6:
                ny_rel = 0.0            # a sliver: treat it as a wall
            else:
                ny_rel = abs(ny) / length
            if ny_rel < 0.35:
                walls.append((a, b, c))
            elif up_only and ny <= 0:
                continue
            elif ceilings and ny < 0 and ny_rel < 0.97:
                downs.append((a, b, c))     # (a sloping ceiling, over stairs: see below)
            else:
                floors.append((a, b, c))
        self.ok = bool(floors)
        pts = [p for tri in (floors or walls) for p in tri]
        xs = [p[0] for p in pts] or [0.0]
        zs = [p[2] for p in pts] or [0.0]
        self.x0 = min(xs) - margin
        self.z0 = min(zs) - margin
        self.w = int((max(xs) + margin - self.x0) / CELL) + 1
        self.h = int((max(zs) + margin - self.z0) / CELL) + 1
        self.heights = {}           # (c, r) -> [ground heights]
        self.sloped = {}            # (c, r, i) whose ground slopes (stairs, ramps) -> how level (1 flat)
        for tri in floors:
            self._lay_floor(tri)
        # (ceilings: a surface facing down with ground a metre or more under it is the ceiling over
        # that ground - the sloping one over the Main Hall's basement stairs read as a ramp from
        # the balcony; one with nothing under it is a floor the file has upside down: kept)
        self.dropped = 0
        for tri in downs:
            cx = (tri[0][0] + tri[1][0] + tri[2][0]) / 3
            cy = (tri[0][1] + tri[1][1] + tri[2][1]) / 3
            cz = (tri[0][2] + tri[1][2] + tri[2][2]) / 3
            under = self.heights.get(self.cell(cx, cz), ())
            if any(cy - 4500.0 < h < cy - 1000.0 for h in under):
                self.dropped += 1
                continue
            self._lay_floor(tri)
        self.walls = []             # (x1, z1, x2, z2, low, high) seen from above
        self.risers = 0             # taller step fronts let through (see _raise_wall)
        self.blocked = set()        # (c, r, i)
        self.near = set()           # free squares that are close to a wall
        for tri in walls:
            self._raise_wall(tri)
        # (no headroom: the ground under a staircase, with the steps a little over it - the walk to
        # the Cavernous Area's Training Room door went in under the stairs and got stuck. Only
        # under sloping ground: under a flat floor - a balcony, a table - is left as it was)
        self.low_head = 0
        self.head_log = [] if HEAD_LOG else None
        lo, hi = HEADROOM
        for (c, r), hs in (self.heights.items() if HEADROOM_ON else ()):
            if len(hs) < 2:
                continue
            for i, h in enumerate(hs):
                if any(lo < h2 - h < hi and (HEADROOM_ON == "any" or (c, r, j) in self.sloped)
                       for j, h2 in enumerate(hs)):
                    if (c, r, i) not in self.blocked:
                        self.blocked.add((c, r, i))
                        self.low_head += 1
                        if self.head_log is not None:
                            self.head_log.append((c, r, h, [(h2, self.sloped.get((c, r, j))) for j, h2 in enumerate(hs)]))
        self.near -= self.blocked

    # ------------------------------------------------------------ building
    def cell(self, x, z):
        return int((x - self.x0) // CELL), int((z - self.z0) // CELL)

    def centre(self, c, r):
        return self.x0 + (c + 0.5) * CELL, self.z0 + (r + 0.5) * CELL

    def _lay_floor(self, tri):
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
        det = (z2 - z3) * (x1 - x3) + (x3 - x2) * (z1 - z3)
        if abs(det) < 1e-6:
            return
        # (sloping: stairs and ramps, not a flat floor - see the headroom check)
        ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
        vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        level = abs(ny) / n
        c0, r0 = self.cell(min(x1, x2, x3), min(z1, z2, z3))
        c1, r1 = self.cell(max(x1, x2, x3), max(z1, z2, z3))
        for r in range(max(r0, 0), min(r1, self.h - 1) + 1):
            for c in range(max(c0, 0), min(c1, self.w - 1) + 1):
                px, pz = self.centre(c, r)
                a = ((z2 - z3) * (px - x3) + (x3 - x2) * (pz - z3)) / det
                b = ((z3 - z1) * (px - x3) + (x1 - x3) * (pz - z3)) / det
                g = 1.0 - a - b
                if a < -0.02 or b < -0.02 or g < -0.02:
                    continue
                y = a * y1 + b * y2 + g * y3
                hs = self.heights.setdefault((c, r), [])
                if not any(abs(y - h) < 120.0 for h in hs):
                    hs.append(y)
                    if level < 0.97 and ny > 0:
                        self.sloped[(c, r, len(hs) - 1)] = level   # (facing up: steps, not a ceiling)

    def _raise_wall(self, tri):
        pts = [(p[0], p[2]) for p in tri]
        ys = [p[1] for p in tri]
        low, high = min(ys), max(ys)
        # Seen from above a wall is a line: its longest side.
        best = None
        for i in range(3):
            for j in range(i + 1, 3):
                d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                if best is None or d > best[0]:
                    best = (d, pts[i], pts[j])
        (x1, z1), (x2, z2) = best[1], best[2]
        self.walls.append((x1, z1, x2, z2, low, high))
        if high - low <= RISER:
            return      # (the front of a stair step: stepped over, not a wall to keep off - the
                        # Large Hall's steep stairs down to the Main Hall door had no way down)
        if high - low <= STEP + 200.0 and self._step_front(x1, z1, x2, z2, low, high):
            self.risers += 1
            return      # (a taller step, with a tread at its foot and one at its top: the Training
                        # Room's stairs up to the control room have 30 cm steps)
        self.block_segment(x1, z1, x2, z2, self.clearance, low, high)
        self.block_segment(x1, z1, x2, z2, COMFORT, low, high, into=self.near)

    def _step_front(self, x1, z1, x2, z2, low, high, tol=60.0):
        """True if ground lies at the wall's top on one side and, no more than a step lower, at
        its foot on the other, all along it: the front of a step, not a kerb or a ledge. (The
        wall can go on below the ground: the bottom step's front.)"""
        length = math.hypot(x2 - x1, z2 - z1)
        if length < 1.0:
            return False
        nx, nz = -(z2 - z1) / length, (x2 - x1) / length
        n = max(1, int(length // CELL))
        for k in range(n + 1):
            t = (k + 0.5) / (n + 1)
            px, pz = x1 + (x2 - x1) * t, z1 + (z2 - z1) * t
            sides = []
            for sgn in (1, -1):
                found = set()
                for d in (0.5 * CELL, 1.0 * CELL, 1.5 * CELL):
                    for h in self.heights.get(self.cell(px + sgn * nx * d, pz + sgn * nz * d), ()):
                        if low - tol <= h <= high - 100.0 and h >= high - STEP - tol:
                            found.add("low")
                        if abs(h - high) <= tol:
                            found.add("high")
                sides.append(found)
            if not (("low" in sides[0] and "high" in sides[1]) or ("high" in sides[0] and "low" in sides[1])):
                return False
        return True

    def block_segment(self, x1, z1, x2, z2, radius, low, high, into=None):
        into = self.blocked if into is None else into
        c0, r0 = self.cell(min(x1, x2) - radius, min(z1, z2) - radius)
        c1, r1 = self.cell(max(x1, x2) + radius, max(z1, z2) + radius)
        dx, dz = x2 - x1, z2 - z1
        length2 = dx * dx + dz * dz
        for r in range(max(r0, 0), min(r1, self.h - 1) + 1):
            for c in range(max(c0, 0), min(c1, self.w - 1) + 1):
                hs = self.heights.get((c, r))
                if not hs:
                    continue
                cx, cz = self.centre(c, r)
                t = max(0.0, min(1.0, ((cx - x1) * dx + (cz - z1) * dz) / length2)) if length2 else 0.0
                px, pz = x1 + t * dx, z1 + t * dz
                if (cx - px) ** 2 + (cz - pz) ** 2 > radius * radius:
                    continue
                for i, h in enumerate(hs):
                    if low < h + BODY[1] and high > h + BODY[0]:
                        into.add((c, r, i))

    def clear_shot(self, x, y, z, ex, ez, height=(1050.0, 1350.0)):
        """True if nothing in the room's walls stands between (x, z) and
        (ex, ez) at gun height above the ground at y."""
        dx, dz = ex - x, ez - z
        length = math.hypot(dx, dz)
        if length < 1.0:
            return True
        # Stop a little short of the enemy: they often stand against a wall.
        k = max(0.0, length - 250.0) / length
        ex, ez = x + dx * k, z + dz * k
        lo, hi = y + height[0], y + height[1]
        for x1, z1, x2, z2, low, high in self.walls:
            if low >= hi or high <= lo:
                continue
            if _crosses(x, z, ex, ez, x1, z1, x2, z2):
                return False
        return True

    def region(self, x, y, z):
        """A number for the part of the room you can walk to from (x, y, z):
        two places with the same number are joined. None off the map."""
        if not hasattr(self, "_regions"):
            self._regions = {}
        node = self.node_at(x, y, z, self.blocked)
        if node is None:
            return None
        if node in self._regions:
            return self._regions[node]
        label = len(set(self._regions.values()))
        todo = [node]
        self._regions[node] = label
        while todo:
            cur = todo.pop()
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    if dc or dr:
                        for nxt in self._steps(cur, dc, dr, self.blocked):
                            if nxt not in self._regions:
                                self._regions[nxt] = label
                                todo.append(nxt)
        return label

    def region_size(self, label):
        return sum(1 for v in self._regions.values() if v == label) if hasattr(self, "_regions") else 0

    def main_floor_near(self, x, z, radius=1200.0, near_y=None):
        """(height, size) of the ground near (x, z) that belongs to the biggest part of the room
        you can walk round (not a pocket shut in by walls), and the size of the biggest part with
        ground about at near_y there; None if no ground."""
        c0, r0 = self.cell(x, z)
        n = int(radius // CELL) + 1
        best = None
        own = 0
        sizes = {}
        for dc in range(-n, n + 1):
            for dr in range(-n, n + 1):
                cx, cz = self.centre(c0 + dc, r0 + dr)
                if (cx - x) ** 2 + (cz - z) ** 2 > radius * radius:
                    continue
                for i, h in enumerate(self.heights.get((c0 + dc, r0 + dr), ())):
                    if (c0 + dc, r0 + dr, i) in self.blocked:
                        continue
                    label = self.region(cx, h, cz)
                    if label is None:
                        continue
                    if label not in sizes:
                        sizes[label] = self.region_size(label)
                    if near_y is not None and abs(h - near_y) <= 300.0:
                        own = max(own, sizes[label])
                    key = (sizes[label], -((cx - x) ** 2 + (cz - z) ** 2))
                    if best is None or key > best[0]:
                        best = (key, h)
        if best is None:
            return None
        return best[1], best[0][0], own

    def joined_near(self, start, goal, radius=1500.0, reach=500.0):
        """True if somewhere within radius of goal (x, y, z), about at its height, is in the same
        part of the room as start: a door whose middle is in the wall still counts as got to."""
        label = self.region(*start)
        if label is None:
            return True
        gx, gy, gz = goal
        c0, r0 = self.cell(gx, gz)
        n = int(radius // CELL) + 1
        for dc in range(-n, n + 1):
            for dr in range(-n, n + 1):
                cx, cz = self.centre(c0 + dc, r0 + dr)
                if (cx - gx) ** 2 + (cz - gz) ** 2 > radius * radius:
                    continue
                for i, h in enumerate(self.heights.get((c0 + dc, r0 + dr), ())):
                    if abs(h - gy) <= reach and self._regions.get((c0 + dc, r0 + dr, i)) == label:
                        return True
        return False

    def free_at(self, x, y, z, reach=300.0):
        """True if someone can stand at (x, z) on ground about at height y."""
        c, r = self.cell(x, z)
        for i, h in enumerate(self.heights.get((c, r), ())):
            if abs(h - y) <= reach and (c, r, i) not in self.blocked:
                return True
        return False

    # ------------------------------------------------------------ searching
    def node_at(self, x, y, z, blocked, limit=12, reach=500.0):
        """The free square nearest (x, z) whose ground is about at height y."""
        c0, r0 = self.cell(x, z)
        best = None
        for d in range(0, limit + 1):
            for dc in range(-d, d + 1):
                for dr in ((-d, d) if abs(dc) != d else range(-d, d + 1)):
                    c, r = c0 + dc, r0 + dr
                    hs = self.heights.get((c, r))
                    if not hs:
                        continue
                    for i, h in enumerate(hs):
                        if abs(h - y) > reach or (c, r, i) in blocked:
                            continue
                        score = dc * dc + dr * dr + (abs(h - y) / CELL) ** 2
                        if best is None or score < best[0]:
                            best = (score, (c, r, i))
            if best is not None and d * d >= best[0]:
                break
        return best[1] if best else None

    def _steps(self, node, dc, dr, blocked):
        """The squares next to `node` that one step can reach (the ground may
        be at more than one height there: under and on a staircase)."""
        c, r, i = node
        h = self.heights[(c, r)][i]
        hs = self.heights.get((c + dc, r + dr))
        if not hs:
            return []
        return [(c + dc, r + dr, j) for j, h2 in enumerate(hs)
                if abs(h2 - h) <= STEP and (c + dc, r + dr, j) not in blocked]

    def _step(self, node, dc, dr, blocked, want=None):
        """One of them: the one whose ground is nearest the height `want`."""
        options = self._steps(node, dc, dr, blocked)
        if not options:
            return None
        if want is None:
            want = self.heights[node[:2]][node[2]]
        return min(options, key=lambda n: abs(self.heights[n[:2]][n[2]] - want))

    def carve(self, points, radius):
        """Open a narrow way the walls' clearance closes (a gap the player does fit through, in
        a cave): squares within `radius` of the line through `points` (x, z) are walkable."""
        for (ax, az), (bx, bz) in zip(points, points[1:]):
            c0, r0 = self.cell(min(ax, bx) - radius, min(az, bz) - radius)
            c1, r1 = self.cell(max(ax, bx) + radius, max(az, bz) + radius)
            vx, vz = bx - ax, bz - az
            ll = vx * vx + vz * vz
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    px, pz = self.centre(c, r)
                    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (pz - az) * vz) / ll))
                    if math.hypot(ax + vx * t - px, az + vz * t - pz) <= radius:
                        for i in range(len(self.heights.get((c, r), ()))):
                            self.blocked.discard((c, r, i))
                            self.near.discard((c, r, i))
        if hasattr(self, "_regions"):
            del self._regions

    def _keeps_off(self, path, i, j, danger):
        """A short cut from path[i] to path[j] doesn't cut in towards a danger's middle (much
        nearer than the squares it replaces went)."""
        ax, az = self.centre(path[i][0], path[i][1])
        bx, bz = self.centre(path[j][0], path[j][1])
        for dx_, dz_, rad, _ in danger:
            vx, vz = bx - ax, bz - az
            ll = vx * vx + vz * vz
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((dx_ - ax) * vx + (dz_ - az) * vz) / ll))
            seg = math.hypot(ax + vx * t - dx_, az + vz * t - dz_)
            if seg >= rad:
                continue
            was = min(math.hypot(self.centre(n[0], n[1])[0] - dx_, self.centre(n[0], n[1])[1] - dz_)
                      for n in path[i:j + 1])
            if seg < min(was - 800.0, was * 0.75):
                return False            # (cutting in more than a metre or so: keep the way round)
        return True

    def line_free(self, a, b, blocked):
        """True if walking straight from square a gets to square b."""
        steps = max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2
        if steps == 0:
            return a == b
        ha = self.heights[a[:2]][a[2]]
        hb = self.heights[b[:2]][b[2]]
        cur = a
        for k in range(1, steps + 1):
            t = k / steps
            c = int(round(a[0] + (b[0] - a[0]) * t))
            r = int(round(a[1] + (b[1] - a[1]) * t))
            dc, dr = c - cur[0], r - cur[1]
            if dc == 0 and dr == 0:
                continue
            if abs(dc) > 1 or abs(dr) > 1:
                return False
            nxt = self._step(cur, dc, dr, blocked, ha + (hb - ha) * t)
            if nxt is None:
                return False
            if dc and dr and (not self._steps(cur, dc, 0, blocked)
                              or not self._steps(cur, 0, dr, blocked)):
                return False
            cur = nxt
        return cur == b

    LEDGE = (400.0, 1300.0)     # a drop (or climb) of this much over an edge: jump down / climb up
    LEDGE_COST = 25.0           # (in squares: only taken when there is no way round)

    def ledge_links(self):
        """{node: [(node across, "down" or "up")]}: edges of raised walkways the player can jump
        down from (into the water of a sewer) or climb up onto, a few squares across."""
        if getattr(self, "_ledges", None) is not None:
            return self._ledges
        links = {}
        lo, hi = self.LEDGE
        for (c, r), hs in self.heights.items():
            for i, h in enumerate(hs):
                node = (c, r, i)
                if node in self.blocked:
                    continue
                for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if self._steps(node, dc, dr, self.blocked):
                        continue                    # (the ground goes on that way)
                    for k in range(1, 6):
                        cc, rr = c + dc * k, r + dr * k
                        hs2 = self.heights.get((cc, rr))
                        if not hs2:
                            break
                        if any(abs(h2 - h) <= STEP and (cc, rr, j) not in self.blocked for j, h2 in enumerate(hs2)):
                            break
                        hit = [(cc, rr, j) for j, h2 in enumerate(hs2)
                               if lo <= abs(h2 - h) <= hi and (cc, rr, j) not in self.blocked]
                        if hit:
                            j = min(hit, key=lambda n: abs(abs(self.heights[n[:2]][n[2]] - h) - 700.0))
                            kind = "down" if self.heights[j[:2]][j[2]] < h else "up"
                            links.setdefault(node, []).append((j, kind))
                            break
        self._ledges = links
        return links

    def set_spots(self, spots):
        """The game's own places to jump down and climb up: [(kind, (x, y, z) of the zone)]. With
        any, only they join the levels (an edge elsewhere cannot be got over, however low). Every
        free square about at the zone's height within reach of it is joined to the nearest square
        of the other level that is open (not under the floor jumped from)."""
        links = {}
        for kind, (x, y, z) in spots:
            c0, r0 = self.cell(x, z)
            here = []
            for dc in range(-8, 9):
                for dr in range(-8, 9):
                    for i, h in enumerate(self.heights.get((c0 + dc, r0 + dr), ())):
                        if abs(h - y) <= 350.0 and (c0 + dc, r0 + dr, i) not in self.blocked:
                            here.append((c0 + dc, r0 + dr, i))
            if not here:
                continue
            h = sum(self.heights[n[:2]][n[2]] for n in here) / len(here)
            best = None
            for dc in range(-20, 21):
                for dr in range(-20, 21):
                    hs = self.heights.get((c0 + dc, r0 + dr), ())
                    if kind == "down" and any(abs(hh - h) <= 350.0 for hh in hs):
                        continue                # (under the floor you jump from: not where you land)
                    for i, h2 in enumerate(hs):
                        node = (c0 + dc, r0 + dr, i)
                        if node in self.blocked:
                            continue
                        if (kind == "down" and h2 < h - 400.0) or (kind == "up" and h2 > h + 400.0):
                            score = dc * dc + dr * dr + abs(h2 - h) / 2000.0
                            if best is None or score < best[0]:
                                best = (score, node)
            if best:
                for n in here:
                    links.setdefault(n, []).append((best[1], kind))
        self.spot_jumps = links
        return links

    def route(self, start, goal, extra=(), danger=(), ledges=False):
        """Waypoints [(x, z)] from start (x, y, z) to goal (x, y, z), or None
        if there is no way. `extra` are circles (x, y, z, radius) to keep
        clear of (enemies). `danger` are circles (x, z, radius, weight) that may be crossed but
        cost more the nearer their middle (Lisa Trevor): a step there counts up to 1 + weight
        times as long, so a longer way round wins unless it is much longer."""
        hot = {}

        def heat(node):
            v = hot.get(node)
            if v is None:
                cx, cz = self.centre(node[0], node[1])
                v = 0.0
                for dx_, dz_, rad, w in danger:
                    d = math.hypot(cx - dx_, cz - dz_)
                    if d < rad:
                        v += w * (1.0 - d / rad)
                hot[node] = v
            return v
        blocked = self.blocked
        near = self.near
        if extra:
            blocked = set(self.blocked)
            near = set(self.near)
            for x, y, z, radius in extra:
                self.block_segment(x, z, x, z, radius, y, y + 1500.0, into=blocked)
                self.block_segment(x, z, x, z, radius + 350.0, y, y + 1500.0, into=near)
            # Never wall in where we stand or where we are going. (Round where we stand, as far
            # as a body's width: standing in a pocket between things to keep clear of - between
            # the chess pieces - the way out starts there.)
            for (px, py, pz), n in ((start, 2), (goal, 1)):
                c, r = self.cell(px, pz)
                for dc in range(-n, n + 1):
                    for dr in range(-n, n + 1):
                        for i in range(len(self.heights.get((c + dc, r + dr), ()))):
                            if (c + dc, r + dr, i) not in self.blocked:
                                blocked.discard((c + dc, r + dr, i))
        s = self.node_at(start[0], start[1], start[2], blocked) or \
            self.node_at(start[0], start[1], start[2], blocked, reach=1e9)
        g = self.node_at(goal[0], goal[1], goal[2], blocked, limit=10, reach=700.0) or \
            self.node_at(goal[0], goal[1], goal[2], blocked, limit=22, reach=700.0) or \
            self.node_at(goal[0], goal[1], goal[2], blocked, limit=10, reach=1e9)
        self.last_start_dy = None
        self.last_end_dy = None
        if s is None or g is None:
            return None
        # (how far from the floor asked for the way starts: a map without the balcony you stand
        # on puts you on the ground under it - a way from there is no way from here)
        self.last_start_dy = abs(self.heights[s[:2]][s[2]] - start[1])
        dist = {s: 0.0}
        came = {}
        heap = [(0.0, s)]
        steps = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
        found = False
        self.last_ledge = None
        jumps = (getattr(self, "spot_jumps", None) or self.ledge_links()) if ledges else {}
        closest = (math.hypot(g[0] - s[0], g[1] - s[1]), s)
        visited = 0
        while heap:
            _, cur = heapq.heappop(heap)
            if cur == g:
                found = True
                break
            left = math.hypot(g[0] - cur[0], g[1] - cur[1])
            if left < closest[0]:
                closest = (left, cur)
            visited += 1
            if visited > 150000:
                break
            base = dist[cur]
            for dc, dr, cost in steps:
                options = self._steps(cur, dc, dr, blocked)
                if not options:
                    continue
                if dc and dr and (not self._steps(cur, dc, 0, blocked)
                                  or not self._steps(cur, 0, dr, blocked)):
                    continue           # no cutting corners
                for nxt in options:
                    nd = base + (cost * NEAR_COST if nxt in near else cost)
                    if danger:
                        nd += cost * heat(nxt)
                    if nd < dist.get(nxt, 1e18):
                        dist[nxt] = nd
                        came[nxt] = cur
                        heapq.heappush(heap, (nd + math.hypot(g[0] - nxt[0], g[1] - nxt[1]), nxt))
            for nxt, _kind in jumps.get(cur, ()):
                if nxt in blocked:
                    continue
                nd = base + self.LEDGE_COST
                if nd < dist.get(nxt, 1e18):
                    dist[nxt] = nd
                    came[nxt] = cur
                    heapq.heappush(heap, (nd + math.hypot(g[0] - nxt[0], g[1] - nxt[1]), nxt))
        if not found:
            # Cannot get all the way: get as near as we can, if that is near.
            if closest[1] == s:
                self.last_end_dy = abs(self.heights[s[:2]][s[2]] - goal[1])
                return [] if closest[0] * CELL <= 1500 else None    # already as near as can be
            if closest[0] * CELL > 3000:
                return None
            g = closest[1]
        self.last_end_dy = abs(self.heights[g[:2]][g[2]] - goal[1])
        path = [g]
        while path[-1] != s:
            path.append(came[path[-1]])
        path.reverse()
        if jumps:
            # (the way only as far as the first edge to jump down or climb up: there the player
            # presses A, and the walk goes on from where that leaves them)
            for k in range(len(path) - 1):
                a, b = path[k], path[k + 1]
                if abs(self.heights[a[:2]][a[2]] - self.heights[b[:2]][b[2]]) > STEP:
                    kind = "down" if self.heights[b[:2]][b[2]] < self.heights[a[:2]][a[2]] else "up"
                    self.last_ledge = (kind, self.centre(a[0], a[1]), self.centre(b[0], b[1]))
                    path = path[:k + 1]
                    break
            if len(path) == 1:
                return []
        # Keep only the corners: go straight wherever nothing is in the way,
        # and do not let a short cut run along a wall the way had kept off.
        wide = blocked | near
        points = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1:
                hugging = path[i] in near or path[j] in near
                if self.line_free(path[i], path[j], blocked if hugging else wide) and \
                        (not hugging or j - i <= 8) and \
                        (not danger or self._keeps_off(path, i, j, danger)):
                    break
                j -= 1
            points.append(path[j])
            i = j
        return [self.centre(c, r) for c, r, _ in points[1:]]
