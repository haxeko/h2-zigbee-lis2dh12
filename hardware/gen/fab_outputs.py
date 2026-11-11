#!/usr/bin/env python3
"""
fab_outputs.py - plots the manufacturing files of the routed board with kicad-cli.

    python3 hardware/gen/fab_outputs.py

Writes to hardware/out/fab/:
    gerbers/                      Gerber X2 for the 4 copper layers, mask, paste, silkscreen, outline
                                  + Excellon drill files (plated and non-plated separately)
    activity-badge-gerbers.zip    the same files, ready to upload to a PCB fabricator
    activity-badge-pos.csv        pick-and-place positions (KiCad column names, millimetres)
and to hardware/out/:
    pcb-front.pdf, pcb-back.pdf   assembly drawings (copper + fabrication layer; back is mirrored)
"""
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


if __name__ == "__main__":
    main()
