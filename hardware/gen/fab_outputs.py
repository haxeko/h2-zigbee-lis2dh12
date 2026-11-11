#!/usr/bin/env python3
"""
fab_outputs.py - plots the manufacturing files of the routed board with kicad-cli.

    python3 hardware/gen/fab_outputs.py

Writes to hardware/out/fab/:
    gerbers/                      Gerber X2 for the 4 copper layers, mask, paste, silkscreen, outline
                                  + Excellon drill files (plated and non-plated separately)
    activity-badge-gerbers.zip    the same files, ready to upload to a PCB fabricator
    activity-badge-pos.csv        pick-and-place positions (KiCad column names, millimetres)
    activity-badge-bom-jlcpcb.csv BOM with the column names JLCPCB's assembly service expects
    activity-badge-cpl-jlcpcb.csv component placement list (CPL), same convention
and to hardware/out/:
    pcb-front.pdf, pcb-back.pdf   assembly drawings (copper + fabrication layer; back is mirrored)
"""
import csv
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
HW = os.path.dirname(HERE)
PCB = os.path.join(HW, "kicad", "activity-badge.kicad_pcb")
OUT = os.path.join(HW, "out")
FAB = os.path.join(OUT, "fab")
GERBERS = os.path.join(FAB, "gerbers")

LAYERS = "F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("command failed: %s\n%s%s" % (" ".join(args), result.stdout, result.stderr))


def package(footprint):
    """Short package name from a KiCad footprint id."""
    name = footprint.split(":")[-1]
    for prefix in ("R_", "C_", "LED_"):
        if name.startswith(prefix):
            return name.split("_")[1]                       # R_0402_1005Metric -> 0402
    for known in ("TSOT-23-5", "SOT-23-5", "SOT-23-6", "SOT-23", "LGA-12"):
        if name.startswith(known):
            return known
    return name


def write_assembly_files(pos_csv):
    """BOM and CPL in the layout of JLCPCB's assembly order form, for the parts in the position file."""
    sys.path.insert(0, HERE)
    import gen_kicad as G

    with open(pos_csv, newline="") as f:
        placed = list(csv.DictReader(f))
    refs = {row["Ref"] for row in placed}

    groups = {}
    for ref, value, _symbol, footprint, mpn, desc, _notes in G.PARTS:
        if ref not in refs:
            continue                                         # pads only: battery pads, test pads
        generic = ref[0] in "RCD"                            # resistors, capacitors, LEDs: value, not one maker's MPN
        comment = desc if generic else (mpn or value)
        for word in ("Resistor ", "Capacitor "):
            if comment.startswith(word):
                comment = comment[len(word):]
        if generic and " (" in comment:
            comment = comment[:comment.index(" (")]          # drop the role so equal values share one line
        groups.setdefault((comment, package(footprint), "" if generic else mpn), []).append(ref)

    bom_path = os.path.join(FAB, "activity-badge-bom-jlcpcb.csv")
    with open(bom_path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "Manufacturer Part #", "Qty"])
        for (comment, pkg, mpn), members in sorted(groups.items(), key=lambda g: G.natural(g[1][0])):
            members.sort(key=G.natural)
            out.writerow([comment, ",".join(members), pkg, "", mpn, len(members)])

    cpl_path = os.path.join(FAB, "activity-badge-cpl-jlcpcb.csv")
    with open(cpl_path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for row in sorted(placed, key=lambda r: G.natural(r["Ref"])):
            out.writerow([row["Ref"], "%.3fmm" % float(row["PosX"]), "%.3fmm" % float(row["PosY"]),
                          "Top" if row["Side"] == "top" else "Bottom", "%g" % float(row["Rot"])])
    return bom_path, cpl_path, len(groups), len(placed)


def main():
    cli = shutil.which("kicad-cli")
    if not cli:
        sys.exit("kicad-cli not found (KiCad 7 or newer is required)")
    if os.path.isdir(GERBERS):
        shutil.rmtree(GERBERS)
    os.makedirs(GERBERS)

    run([cli, "pcb", "export", "gerbers", "--output", GERBERS + os.sep, "--layers", LAYERS,
         "--subtract-soldermask", PCB])
    run([cli, "pcb", "export", "drill", "--output", GERBERS + os.sep, "--format", "excellon",
         "--excellon-units", "mm", "--excellon-separate-th", "--drill-origin", "absolute", PCB])
    run([cli, "pcb", "export", "pos", "--output", os.path.join(FAB, "activity-badge-pos.csv"), "--format", "csv",
         "--units", "mm", "--side", "both", "--smd-only", PCB])
    run([cli, "pcb", "export", "pdf", "--output", os.path.join(OUT, "pcb-front.pdf"),
         "--layers", "F.Cu,F.Fab,Edge.Cuts", PCB])
    run([cli, "pcb", "export", "pdf", "--output", os.path.join(OUT, "pcb-back.pdf"),
         "--layers", "B.Cu,B.SilkS,Edge.Cuts", "--mirror", PCB])

    files = sorted(os.listdir(GERBERS))
    zip_path = os.path.join(FAB, "activity-badge-gerbers.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files:
            z.write(os.path.join(GERBERS, name), name)
    print("%d fabrication files in %s" % (len(files), os.path.relpath(GERBERS, os.path.dirname(HW))))
    for name in files:
        print("   " + name)
    print("zip: %s" % os.path.relpath(zip_path, os.path.dirname(HW)))
    bom_path, cpl_path, n_lines, n_parts = write_assembly_files(os.path.join(FAB, "activity-badge-pos.csv"))
    print("assembly: %s (%d lines), %s (%d parts)" % (os.path.basename(bom_path), n_lines,
                                                      os.path.basename(cpl_path), n_parts))


if __name__ == "__main__":
    main()
