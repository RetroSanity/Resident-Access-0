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
    def __init__(self, triangles, margin=300.0, clearance=CLEARANCE):
        """triangles: [((x, y, z), (x, y, z), (x, y, z))]. clearance: how far from a wall the
        player's middle keeps (a tighter mesh squeezes through narrow gaps)."""
        self.clearance = clearance
        floors, walls = [], []
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
        for tri in floors:
            self._lay_floor(tri)
        self.walls = []             # (x1, z1, x2, z2, low, high) seen from above
        self.blocked = set()        # (c, r, i)
        self.near = set()           # free squares that are close to a wall
        for tri in walls:
            self._raise_wall(tri)
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
        self.block_segment(x1, z1, x2, z2, self.clearance, low, high)
        self.block_segment(x1, z1, x2, z2, COMFORT, low, high, into=self.near)

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

    def route(self, start, goal, extra=(), danger=()):
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
            # Never wall in where we stand or where we are going.
            for px, py, pz in (start, goal):
                c, r = self.cell(px, pz)
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        for i in range(len(self.heights.get((c + dc, r + dr), ()))):
                            if (c + dc, r + dr, i) not in self.blocked:
                                blocked.discard((c + dc, r + dr, i))
        s = self.node_at(start[0], start[1], start[2], blocked) or \
            self.node_at(start[0], start[1], start[2], blocked, reach=1e9)
        g = self.node_at(goal[0], goal[1], goal[2], blocked, limit=10, reach=700.0) or \
            self.node_at(goal[0], goal[1], goal[2], blocked, limit=22, reach=700.0) or \
            self.node_at(goal[0], goal[1], goal[2], blocked, limit=10, reach=1e9)
        if s is None or g is None:
            return None
        dist = {s: 0.0}
        came = {}
        heap = [(0.0, s)]
        steps = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                 (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]
        found = False
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
        if not found:
            # Cannot get all the way: get as near as we can, if that is near.
            if closest[1] == s:
                return [] if closest[0] * CELL <= 1500 else None    # already as near as can be
            if closest[0] * CELL > 3000:
                return None
            g = closest[1]
        path = [g]
        while path[-1] != s:
            path.append(came[path[-1]])
        path.reverse()
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
