"""Reading a room's collision shapes (walls, floors, stairs) from the game's
own files: nativePC/arc/room/stN/rNXX.arc holds room/sbc/.../rNXX, an MT
Framework "SBC" file. Its triangles are 32 bytes each (a normal and three
corner numbers), straight before the corners (16 bytes each)."""
import os
import struct
import zlib


def read_arc(path):
    with open(path, "rb") as fh:
        d = fh.read()
    magic, ver, n = struct.unpack_from("<4sHH", d, 0)
    if magic != b"ARC\0":
        raise ValueError("not an archive")
    out = {}
    off = 8
    for _ in range(n):
        name = d[off:off + 64].split(b"\0")[0].decode("ascii", "replace")
        typ, csize, size, foff = struct.unpack_from("<IIII", d, off + 64)
        off += 80
        out[name] = (typ, foff, csize)
    return d, out


def unpack(d, entry):
    typ, foff, csize = entry
    raw = d[foff:foff + csize]
    try:
        return zlib.decompress(raw)
    except zlib.error:
        return raw


def triangles(sbc):
    """[((x, y, z), (x, y, z), (x, y, z))] in the game's units (centimetres)."""
    if sbc[:3] != b"SBC":
        return []
    ntris, nverts = struct.unpack_from("<II", sbc, 0x18)
    if not (0 < ntris < 200000 and 0 < nverts < 200000):
        return []
    # The corners are the last thing but a table after them; find where the
    # triangles (each with a unit normal and corner numbers below nverts) end.
    need = ntris * 32 + nverts * 16
    for start in range(0xA0, len(sbc) - need + 1, 4):
        ok = True
        for i in (0, ntris // 2, ntris - 1):
            o = start + i * 32
            nx, ny, nz = struct.unpack_from("<3f", sbc, o + 4)
            a, b, c = struct.unpack_from("<3H", sbc, o + 16)
            if abs(nx * nx + ny * ny + nz * nz - 1) > 0.02 or max(a, b, c) >= nverts:
                ok = False
                break
        if not ok:
            continue
        vo = start + ntris * 32
        verts = [struct.unpack_from("<3f", sbc, vo + i * 16 + 4) for i in range(nverts)]
        out = []
        for i in range(ntris):
            a, b, c = struct.unpack_from("<3H", sbc, start + i * 32 + 16)
            if max(a, b, c) < nverts:
                out.append((verts[a], verts[b], verts[c]))
        return out
    return []


def room_triangles(game_dir, area, room):
    path = os.path.join(game_dir, "nativePC", "arc", "room", "st%02d" % area, "r%x%02x.arc" % (area, room))
    if not os.path.exists(path):
        return []
    d, entries = read_arc(path)
    base = "room\\sbc\\st%02d\\r%x%02x\\r%x%02x" % (area, area, room, area, room)
    tris = []
    for name, entry in entries.items():
        if name.lower() == base.lower():
            tris += triangles(unpack(d, entry))
    return tris
