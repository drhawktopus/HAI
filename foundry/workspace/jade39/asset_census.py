r"""#39 Phase 1: archive-level asset census of a Jade Empire install - every resource inside chitin.key's BIFs, the
data/**/*.rim archives and the .mod/.erf archives, plus the loose override files, by type.

Usage: asset_census.py <game_dir> <out_dir> [order]

Formats, from the recompile's own tools (plaught-armor/jade-empire-recomp @ 4d8459e, docs/modding.md s1):
  KEY V1   u32 bif count @8, res count @12, bif table @16, res table @20; bif entries 12 bytes (u32 size, u32 name
           offset, u16 name length, u16 drives); res entries 22 bytes (char resref[16], u16 type, u32 id =
           bif << 20 | index)                                                       (tools/dump_tex/dump_tex.odin)
  BIFF V1  u32 resource count @8, table @16; entries 16 bytes (u32 id, offset, size, type)
  RIM V1.0 u32 entry count @12, table @16; entries 32 bytes (char resref[16], u16 type, u16 0, u32 index,
           u32 offset, u32 size)                                                    (tools/rim/rim.odin)
  MOD/ERF  NOT in the recompile's docs; read with the standard BioWare ERF V1.0 layout (entry count @16, key list
           offset @24, resource list offset @28; keys 24 bytes resref[16] u32 id u16 type u16; resources 8 bytes
           u32 offset u32 size), every offset checked against the file. A file that does not fit is reported as
           UNPARSED with the reason - never counted as empty.
Jade's type ids differ from KotOR's; names from docs/modding.md (3011 is unidentified there).

Positive controls (printed and recorded, never silently skipped): the docs' 7597 unique textures (type 3017 in the
BIFs and RIMs, plus override .txb names) and 5516 entries of type 3011.

Deliverables in out_dir: entries.csv (one row per resource), census_summary.json, progress\*.done.
Read-only: nothing in the game folder is written.
"""
import collections
import csv
import json
import os
import struct
import sys
import time

TYPES = {2002: "mdl", 2010: "ncs", 2017: "2da", 2026: "cre", 2029: "dlg", 2031: "trg", 2043: "pla", 2053: "pwk",
         3000: "lyt", 3001: "vis", 3012: "qst", 3013: "sto", 3015: "hex", 3016: "mdx", 3017: "tpc", 3022: "fsm",
         3023: "scn"}
KNOWN_TEXTURES, KNOWN_3011 = 7597, 5516  # docs/modding.md s1


def ext(ty):
    return TYPES.get(ty, str(ty))


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def resref(raw):
    z = raw.find(b"\x00")
    return (raw[:z] if z >= 0 else raw).decode("latin-1").lower()


def done(out, step, data=None):
    os.makedirs(os.path.join(out, "progress"), exist_ok=True)
    with open(os.path.join(out, "progress", step + ".done"), "w") as f:
        json.dump(data or {}, f)
    print("STEP", step, "done", flush=True)


def parse_key_bifs(game):
    """[(archive, resref, type, size)] for every BIF resource the key names, plus key/bif consistency counts."""
    key = open(os.path.join(game, "chitin.key"), "rb").read()
    if key[:8] != b"KEY V1  ":
        raise ValueError("chitin.key: not KEY V1 (%r)" % key[:8])
    nbif, nres, off_b, off_r = u32(key, 8), u32(key, 12), u32(key, 16), u32(key, 20)
    bifs = []
    for i in range(nbif):
        e = off_b + 12 * i
        noff, nlen = u32(key, e + 4), u16(key, e + 8)
        bifs.append(key[noff:noff + nlen].rstrip(b"\x00").decode("latin-1").replace("\\", "/"))
    names = {}
    for i in range(nres):
        e = off_r + 22 * i
        names[u32(key, e + 18)] = resref(key[e:e + 16])
    rows, unnamed = [], 0
    for bi, rel in enumerate(bifs):
        b = open(os.path.join(game, rel), "rb").read()
        if b[:8] != b"BIFFV1  ":
            raise ValueError("%s: not BIFF V1 (%r)" % (rel, b[:8]))
        n, table = u32(b, 8), u32(b, 16)
        for j in range(n):
            e = table + 16 * j
            off, size, ty = u32(b, e + 4), u32(b, e + 8), u32(b, e + 12)
            if off + size > len(b):
                raise ValueError("%s entry %d out of range" % (rel, j))
            ref = names.get(bi << 20 | j)
            if ref is None:
                unnamed += 1
                ref = "?%d_%d" % (bi, j)
            rows.append((rel, ref, ty, size))
    return rows, {"key_resources": nres, "bifs": bifs, "bif_entries": len(rows), "bif_entries_unnamed": unnamed}


def parse_rim(path):
    d = open(path, "rb").read()
    if d[:8] != b"RIM V1.0":
        raise ValueError("not RIM V1.0 (%r)" % d[:8])
    n, table = u32(d, 12), u32(d, 16)
    if table + 32 * n > len(d):
        raise ValueError("entry table out of range (%d at %#x)" % (n, table))
    out = []
    for i in range(n):
        e = table + 32 * i
        ty, off, size = u16(d, e + 16), u32(d, e + 24), u32(d, e + 28)
        if off + size > len(d):
            raise ValueError("entry %d data out of range" % i)
        out.append((resref(d[e:e + 16]), ty, size))
    return out


def parse_erf(path):
    d = open(path, "rb").read()
    magic = d[:8]
    if magic[4:8] != b"V1.0" or magic[:4] not in (b"MOD ", b"ERF ", b"SAV ", b"HAK "):
        raise ValueError("not an ERF V1.0-family file (%r)" % magic)
    n, keys, res = u32(d, 16), u32(d, 24), u32(d, 28)
    if keys + 24 * n > len(d) or res + 8 * n > len(d):
        raise ValueError("tables out of range (n=%d keys=%#x res=%#x size=%d)" % (n, keys, res, len(d)))
    out = []
    for i in range(n):
        k, r = keys + 24 * i, res + 8 * i
        ty, off, size = u16(d, k + 20), u32(d, r), u32(d, r + 4)
        if off + size > len(d):
            raise ValueError("entry %d data out of range" % i)
        out.append((resref(d[k:k + 16]), ty, size))
    return out


def main():
    game, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    rows, failures = [], []

    # 1 key + bifs
    bif_rows, key_info = parse_key_bifs(game)
    rows += [("bif", a, r, t, s) for a, r, t, s in bif_rows]
    done(out, "1_key_bif", key_info)

    # 2 rims, mods/erfs (any depth under the game folder)
    rims, erfs = [], []
    for root, _dirs, files in os.walk(game):
        for fn in files:
            low = fn.lower()
            p = os.path.join(root, fn)
            if low.endswith(".rim"):
                rims.append(p)
            elif low.endswith((".mod", ".erf", ".hak", ".sav")):
                erfs.append(p)
    for kind, paths, parse in (("rim", sorted(rims), parse_rim), ("erf", sorted(erfs), parse_erf)):
        for p in paths:
            rel = os.path.relpath(p, game).replace("\\", "/")
            try:
                rows += [(kind, rel, r, t, s) for r, t, s in parse(p)]
            except Exception as exc:
                failures.append({"archive": rel, "kind": kind, "reason": str(exc)[:200]})
    done(out, "2_archives", {"rims": len(rims), "erfs": len(erfs), "unparsed": len(failures)})

    # 3 loose override files
    loose = collections.Counter()
    override_names = set()
    od = os.path.join(game, "override")
    for root, _dirs, files in os.walk(od):
        for fn in files:
            base, e = os.path.splitext(fn.lower())
            loose[e.lstrip(".")] += 1
            override_names.add((base, e.lstrip(".")))
    done(out, "3_override", {"files": sum(loose.values())})

    # 4 summary + positive controls
    with open(os.path.join(out, "entries.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "archive", "resref", "type", "ext", "size"])
        for kind, a, r, t, s in rows:
            w.writerow([kind, a, r, t, ext(t), s])
    by_type = collections.defaultdict(lambda: {"entries": 0, "unique": set(), "bytes": 0, "by_kind": collections.Counter()})
    for kind, _a, r, t, s in rows:
        bt = by_type[ext(t)]
        bt["entries"] += 1
        bt["unique"].add(r)
        bt["bytes"] += s
        bt["by_kind"][kind] += 1
    types = {e: {"entries": v["entries"], "unique_resrefs": len(v["unique"]), "bytes": v["bytes"],
                 "by_kind": dict(v["by_kind"])}
             for e, v in sorted(by_type.items(), key=lambda kv: -kv[1]["entries"])}
    packed_tpc = {r for kind, _a, r, t, _s in rows if t == 3017 and kind in ("bif", "rim")}
    tex_total = len(packed_tpc | {b for b, e in override_names if e == "txb"})
    n3011 = sum(1 for _k, _a, _r, t, _s in rows if t == 3011)
    overrides_packed = sum(1 for b, e in override_names
                           if b in by_type.get("tpc" if e == "txb" else e, {}).get("unique", set()))
    summary = {
        "game": game, "seconds": round(time.time() - t0, 1),
        "archives": {"bif": len(key_info["bifs"]), "rim": len(rims), "erf_family": len(erfs),
                     "unparsed": failures},
        "resources_total": len(rows),
        "key": key_info,
        "types": types,
        "override_loose_by_ext": dict(loose.most_common()),
        "override_files_replacing_a_packed_resource": overrides_packed,
        "positive_controls": {
            "textures_unique_bif_rim_plus_override_txb": {"measured": tex_total, "docs": KNOWN_TEXTURES,
                                                          "match": tex_total == KNOWN_TEXTURES},
            "type_3011_entries": {"measured": n3011, "docs": KNOWN_3011, "match": n3011 == KNOWN_3011},
        },
    }
    with open(os.path.join(out, "census_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    done(out, "4_summary", {"resources": len(rows), "controls": summary["positive_controls"]})
    print(json.dumps({"resources": len(rows), "unparsed": len(failures),
                      "controls": summary["positive_controls"]}), flush=True)
    return 0 if rows and not (key_info["bif_entries"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
