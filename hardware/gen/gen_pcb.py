#!/usr/bin/python3
"""
gen_pcb.py - lays out the Activity Badge PCB with KiCad's pcbnew Python API.

Input : hardware/out/activity-badge.net (written by gen_kicad.py) and the
        placement table PLACE below.
Output: hardware/kicad/activity-badge.kicad_pcb, fully routed, zones filled,
        plus a DRC report in hardware/out/. Fabrication files are plotted by
        fab_outputs.py.

Board: 20 x 36 mm, 4 copper layers (F.Cu signals + parts / In1 GND plane /
In2 +3V3 plane / B.Cu signals + GND pour), vias 0.6 mm with a 0.3 mm hole, all
parts on the top side, battery
pads and test pads on the bottom. The module's antenna overhangs the top edge.

Routing: the fine-pitch escapes (USB-C, accelerometer), the module decoupling
and the vias next to the module pads are drawn by hand in pre_routes(); the
rest is found by the maze router in router.py. KiCad's DRC and a pad-by-pad
comparison with the schematic netlist run at the end; the exit code is 0 only
if both are clean.

Run with the Python that ships KiCad's pcbnew module (the system Python on
Linux, KiCad's bundled one on Windows/macOS):
    /usr/bin/python3 hardware/gen/gen_pcb.py
"""
import math
import os
import subprocess
import sys
import time

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_kicad as G                       # noqa: E402  (design data, paths, rules)
from router import PITCH, Router, Shape     # noqa: E402

PCB_PATH = os.path.join(G.KICAD_DIR, G.PROJECT + ".kicad_pcb")
NET_PATH = os.path.join(G.OUT_DIR, G.PROJECT + ".net")
DRC_PATH = os.path.join(G.OUT_DIR, "drc-report.txt")

# ---------------------------------------------------------------------------
# Board definition
# ---------------------------------------------------------------------------
BOARD_W, BOARD_H = 20.0, 36.0
CORNER_R = 0.8
CLEARANCE = G.PCB_RULES["min_clearance"]
EDGE_CLEARANCE = G.PCB_RULES["min_copper_edge_clearance"]
W_SIGNAL, W_POWER, W_FINE = G.PCB_TRACK["signal"], G.PCB_TRACK["power"], 0.2
VIA_D, VIA_DRILL = G.PCB_RULES["min_via_diameter"], G.PCB_RULES["min_through_hole_diameter"]
# Router layer indices. Signals run on the front, the back and, where those two
# are not enough, on the second inner layer (the +3V3 pour flows around them).
# The first inner layer stays an unbroken ground plane.
F, B, I = 0, 1, 2
ALL = (F, B, I)
KICAD_LAYER = {F: pcbnew.F_Cu, B: pcbnew.B_Cu, I: pcbnew.In2_Cu}
INNER_COST = 2.5                             # an inner-layer track counts 2.5x its length

# Nets carried by the inner planes: every pad gets a short stub and a via.
PLANE_NETS = {"GND": pcbnew.In1_Cu, "+3V3": pcbnew.In2_Cu}
POWER_NETS = {"VBUS", "VBAT"}

# ref: (x, y, rotation in degrees, side). Coordinates in mm from the top-left
# board corner. U1 sits with its antenna (6 mm) hanging over the top edge.
PLACE = {
    "U1": (10.0, 4.0, 0, "F"),
    "J1": (10.0, 33.0, 0, "F"),
    "SW1": (2.6, 32.6, 270, "F"),
    "SW2": (17.4, 32.6, 270, "F"),
    # accelerometer + its decoupling and I2C pull-ups
    "U2": (3.8, 17.5, 0, "F"),
    "R6": (1.5, 16.55, 0, "F"),
    "R5": (1.5, 18.45, 0, "F"),
    "C9": (6.0, 16.75, 0, "F"),
    "C7": (6.0, 17.75, 0, "F"),
    "C8": (6.8, 19.05, 0, "F"),
    # charger
    "D2": (1.9, 20.6, 0, "F"),
    "R4": (4.6, 20.6, 180, "F"),
    "U4": (3.2, 23.1, 0, "F"),
    "R3": (6.3, 22.15, 0, "F"),
    "C1": (6.3, 26.2, 90, "F"),
    "C2": (2.7, 25.8, 0, "F"),
    # boot button support, strap pull-up
    "R8": (2.7, 27.7, 0, "F"),
    "C11": (1.5, 29.3, 180, "F"),
    "R7": (3.7, 29.3, 180, "F"),
    # module decoupling right at pins 13 (GND) / 14 (3V3), status LED at pin 15
    "C6": (8.5, 15.5, 180, "F"),
    "C5": (8.5, 16.75, 180, "F"),
    "R9": (11.2, 15.5, 0, "F"),
    "D1": (13.75, 15.6, 180, "F"),
    # USB: series resistors, ESD array, CC pull-downs
    "R15": (9.3, 21.8, 90, "F"),
    "R14": (10.7, 21.8, 90, "F"),
    "U5": (10.0, 25.0, 270, "F"),
    "R1": (7.67, 26.75, 90, "F"),
    "R2": (12.33, 26.75, 90, "F"),
    # switched battery divider
    "R11": (11.1, 18.3, 270, "F"),
    "Q2": (13.6, 18.7, 0, "F"),
    "Q1": (17.5, 18.7, 0, "F"),
    "R10": (17.5, 16.3, 180, "F"),
    "R13": (13.3, 20.95, 180, "F"),
    "C10": (15.3, 20.95, 180, "F"),
    "R12": (17.3, 20.95, 180, "F"),
    # LDO
    "C4": (17.0, 22.5, 180, "F"),
    "U3": (16.3, 25.3, 0, "F"),
    "C3": (13.2, 24.35, 180, "F"),
    # bottom side: battery lead pads under the USB connector, test pads
    "J2": (10.0, 33.1, 0, "B"),
    "TP6": (4.0, 2.4, 0, "B"),
    "TP1": (6.5, 2.4, 0, "B"),
    "TP2": (9.0, 2.4, 0, "B"),
    "TP7": (11.5, 2.4, 0, "B"),
    "TP5": (14.0, 2.4, 0, "B"),
    "TP4": (16.5, 2.4, 0, "B"),
    "TP3": (17.0, 29.0, 0, "B"),
}

# Areas on the top layer closed to routed tracks: under the accelerometer (ST:
# no copper under the LGA), under the USB shell, and under the module body
# (its underside stays ground pour; module signals drop to the bottom layer
# through the via fence defined in pre_routes()).
KEEPOUT_F = [(3.25, 16.95, 4.35, 18.05), (5.7, 29.8, 14.3, BOARD_H), (2.1, 0.0, 17.9, 12.9)]

# Bottom silkscreen labels for the test pads.
TP_LABEL = {"TP1": "3V3", "TP2": "GND", "TP3": "VBAT", "TP4": "TX", "TP5": "RX", "TP6": "EN", "TP7": "BOOT"}

# Module side pads that carry a net: pin -> y. Each gets a stub and a via ("via fence").
MODULE_LEFT_X, MODULE_RIGHT_X, FENCE_INSET = 1.25, 18.75, 1.2


def pre_routes():
    """
    Hand-designed copper for the places a maze router handles badly: the 0.5 mm
    pitch escapes of the USB-C connector and the accelerometer, the module's
    decoupling and the via fence along its side pads. Coordinates are absolute
    (mm) and follow from PLACE; change both together.
    Returns (tracks, vias): tracks = [(net, layer, width, [(x, y), ...])], vias = [(net, x, y)].
    """
    t, v = [], []
    s, f, p = W_SIGNAL, W_FINE, W_POWER

    # --- module via fence: pins 1..9 on the left, 20..28 on the right (pin 1 and 28 at the top)
    left = {1: "+3V3", 2: "EN", 3: "I2C_SDA", 4: "I2C_SCL", 5: "ACC_INT1", 6: "ACC_INT2", 7: "IO8_STRAP",
            8: "BOOT", 9: "GND"}
    right = {28: "VBAT_SENSE_EN", 27: "VBAT_SENSE", 24: "USB_DP", 23: "USB_DN", 22: "UART_TX", 21: "UART_RX"}
    for pin, net in left.items():
        y = 1.1 + 1.5 * (pin - 1)
        inset = 1.05 if pin == 9 else FENCE_INSET       # pin 9: stay clear of bottom-row pad 10
        t.append((net, F, p if net in PLANE_NETS else f, [(MODULE_LEFT_X, y), (MODULE_LEFT_X + inset, y)]))
        v.append((net, MODULE_LEFT_X + inset, y))
    for pin, net in right.items():
        y = 1.1 + 1.5 * (28 - pin)
        t.append((net, F, f, [(MODULE_RIGHT_X, y), (MODULE_RIGHT_X - FENCE_INSET, y)]))
        v.append((net, MODULE_RIGHT_X - FENCE_INSET, y))
    v += [("GND", 9.0, 7.73), ("GND", 10.06, 7.73)]          # ground pad

    # --- module decoupling: pin 13 (GND) / pin 14 (3V3) straight down through C6 into C5
    t += [("+3V3", F, p, [(9.1, 14.3), (9.1, 16.6)]), ("+3V3", F, p, [(9.1, 14.95), (9.75, 14.95)]),
          ("GND", F, p, [(7.9, 14.3), (7.9, 16.6)]), ("GND", F, p, [(7.9, 14.95), (7.25, 14.95)])]
    v += [("+3V3", 9.75, 14.95), ("GND", 7.25, 14.95)]

    # --- accelerometer U2 (3.8, 17.5): I2C to the pull-ups, supplies to the capacitors, INT stubs
    t += [("I2C_SCL", F, s, [(3.04, 16.75), (2.1, 16.75)]),
          ("I2C_SDA", F, s, [(3.04, 18.25), (2.1, 18.25)]),
          ("+3V3", F, f, [(3.04, 17.25), (2.7, 17.25), (2.7, 17.75), (3.04, 17.75)]),       # CS + SA0
          ("+3V3", F, f, [(2.7, 17.5), (2.35, 17.5)]),
          ("+3V3", F, p, [(0.99, 16.7), (0.99, 18.3)]),                                     # R6.1 - R5.1
          ("+3V3", F, f, [(4.56, 16.75), (5.4, 16.75)]),                                    # VDD_IO - C9
          ("+3V3", F, f, [(4.56, 17.25), (5.1, 17.25), (5.4, 17.55)]),                      # VDD - C7
          ("+3V3", F, p, [(5.52, 15.85), (5.52, 17.6)]),                                    # C9 - C7 + via
          ("GND", F, s, [(4.55, 17.75), (4.55, 18.65), (4.05, 19.15)]),                     # pins 8, 7
          ("GND", F, s, [(4.05, 18.3), (4.05, 19.15)]),                                     # pin 6
          ("GND", F, s, [(3.55, 18.3), (3.55, 18.65), (4.05, 19.15)]),                      # pin 5 (RES)
          ("ACC_INT1", F, s, [(3.55, 16.7), (3.55, 15.9), (3.1, 15.45)]),
          ("ACC_INT2", F, s, [(4.05, 16.7), (4.05, 15.9), (4.5, 15.45)])]
    v += [("+3V3", 2.35, 17.5), ("+3V3", 0.99, 17.5), ("+3V3", 5.52, 15.85), ("GND", 4.05, 19.15),
          ("ACC_INT1", 3.1, 15.45), ("ACC_INT2", 4.5, 15.45)]

    # --- landing vias: where the module signals come back up from the bottom layer.
    #     Fixed here because free via sites are scarce between the parts on the top side.
    t += [("I2C_SCL", F, s, [(2.0, 16.4), (2.0, 15.5)]),
          ("I2C_SDA", F, s, [(2.0, 18.6), (2.0, 19.35)]),
          ("IO8_STRAP", F, s, [(3.3, 27.7), (4.2, 27.7)]),
          ("BOOT", F, s, [(1.98, 29.3), (3.19, 29.3)]),                # C11 - R7
          ("BOOT", F, s, [(2.6, 30.6), (2.6, 28.75)]),                 # SW1 - via
          ("EN", F, s, [(18.0, 30.6), (18.0, 29.85)]),                 # SW2 - via
          ("USB_DN", F, s, [(9.3, 21.2), (9.3, 20.5)]),
          ("USB_DP", F, s, [(10.7, 21.2), (10.7, 20.5)]),
          ("VBAT_SENSE_EN", F, s, [(11.1, 17.6), (11.1, 16.9)])]
    v += [("I2C_SCL", 2.0, 15.5), ("I2C_SDA", 2.0, 19.35), ("IO8_STRAP", 4.2, 27.7), ("BOOT", 2.6, 28.75),
          ("EN", 18.0, 29.85), ("USB_DN", 9.3, 20.5), ("USB_DP", 10.7, 20.5), ("VBAT_SENSE_EN", 11.1, 16.9)]

    # --- USB-C J1 (10, 33) with U5 (10, 25), R1, R2, C1. The inner D- pad joins the
    #     outer one above the D+ pad, whose own link goes through a via to the bottom layer.
    #     B7 steps aside early so that a 0.6 mm via fits between it and A7.
    t += [("USB_CONN_DN", F, s, [(9.25, 28.6), (9.25, 28.15), (9.05, 27.95), (9.05, 27.15), (9.05, 26.3)]),  # B7 -> U5.4
          ("USB_CONN_DP", F, s, [(10.75, 28.6), (10.75, 27.35), (10.95, 27.15), (10.95, 26.3)]),  # B6 -> U5.6
          ("USB_CONN_DP", F, s, [(9.75, 28.6), (9.75, 28.2), (9.65, 28.1), (9.65, 27.75)]),       # A6 -> via
          ("USB_CONN_DN", F, s, [(10.25, 28.6), (10.25, 27.45), (9.95, 27.15), (9.05, 27.15)]),   # A7 -> B7
          ("USB_CONN_DN", F, s, [(9.05, 26.0), (9.05, 24.0)]),                                    # U5.4 - U5.3
          ("USB_CONN_DP", F, s, [(10.95, 26.0), (10.95, 24.0)]),                                  # U5.6 - U5.1
          ("USB_CONN_DN", F, s, [(9.05, 23.4), (9.05, 22.9), (9.3, 22.65), (9.3, 22.4)]),         # U5.3 - R15.1
          ("USB_CONN_DP", F, s, [(10.95, 23.4), (10.95, 22.9), (10.7, 22.65), (10.7, 22.4)]),     # U5.1 - R14.1
          ("CC1", F, s, [(8.75, 28.6), (8.75, 28.2), (7.9, 27.35)]),
          ("CC2", F, s, [(11.75, 28.6), (11.75, 27.95), (12.2, 27.5), (12.2, 27.35)]),
          ("VBUS", F, p, [(7.55, 28.6), (7.55, 27.9), (6.95, 27.9), (6.6, 27.55), (6.6, 27.25)]),  # -> C1
          ("VBUS", F, p, [(12.45, 28.6), (12.45, 27.9), (13.0, 27.9), (13.3, 27.6)]),             # -> via
          ("VBUS", F, f, [(10.0, 25.05), (10.0, 25.7)])]                                          # U5.5 -> via
    v += [("USB_CONN_DP", 9.65, 27.75), ("VBUS", 13.3, 27.6), ("VBUS", 10.0, 25.05)]
    return t, v


def mm(v):
    return pcbnew.FromMM(float(v))


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


# ---------------------------------------------------------------------------
# Netlist
# ---------------------------------------------------------------------------
def read_netlist():
    """Return ({ref: symbol uuid}, {(ref, pad): net name}) from the KiCad netlist."""
    tree = G.sx_parse(open(NET_PATH).read())

    def children(node, key):
        return [c for c in node if isinstance(c, list) and c and c[0] == key]

    def child(node, key):
        found = children(node, key)
        return found[0] if found else None

    stamps, pad_net = {}, {}
    for comp in children(child(tree, "components"), "comp"):
        stamps[str(child(comp, "ref")[1])] = str(child(comp, "tstamps")[1])
    for net in children(child(tree, "nets"), "net"):
        name = str(child(net, "name")[1])
        for node in children(net, "node"):
            pad_net[(str(child(node, "ref")[1]), str(child(node, "pin")[1]))] = name
    return stamps, pad_net


def short(net_name):
    """Schematic label nets are exported as '/NAME'."""
    return net_name[1:] if net_name.startswith("/") else net_name


# ---------------------------------------------------------------------------
# Board construction
# ---------------------------------------------------------------------------
def add_outline(board):
    r, w, h = CORNER_R, BOARD_W, BOARD_H

    def shape():
        s = pcbnew.PCB_SHAPE(board)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)
        return s

    for (x0, y0, x1, y1) in ((r, 0, w - r, 0), (w, r, w, h - r), (w - r, h, r, h), (0, h - r, 0, r)):
        s = shape()
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(x0, y0))
        s.SetEnd(pt(x1, y1))
    k = r * (1 - math.sqrt(0.5))
    for (sx, sy, mx, my, ex, ey) in ((0, r, k, k, r, 0), (w - r, 0, w - k, k, w, r),
                                     (w, h - r, w - k, h - k, w - r, h), (r, h, k, h - k, 0, h - r)):
        s = shape()
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(pt(sx, sy), pt(mx, my), pt(ex, ey))


def load_footprint(fpid):
    lib, name = fpid.split(":")
    path = os.path.join(G.KICAD_DIR, "badge.pretty") if lib == "badge" else os.path.join(G.FP_DIR, lib + ".pretty")
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit("footprint not found: " + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def build_board():
    stamps, pad_net = read_netlist()
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().SetBoardThickness(mm(1.6))
    add_outline(board)

    nets = {}
    for name in sorted(set(pad_net.values())):
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = item

    for ref, value, _symbol, fpid, _mpn, _desc, _notes in G.PARTS:
        x, y, rot, side = PLACE[ref]
        fp = load_footprint(fpid)
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPath(pcbnew.KIID_PATH("/" + stamps[ref]))
        board.Add(fp)
        fp.SetPosition(pt(x, y))
        if side == "B":
            fp.Flip(pt(x, y), False)
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            name = pad_net.get((ref, pad.GetNumber()))
            if name:
                pad.SetNet(nets[name])
        # The board is too dense for reference designators on silkscreen: they stay on the Fab layer.
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        for item in list(fp.GraphicalItems()):
            if item.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                box = item.GetBoundingBox()
                if (ref == "U1" or pcbnew.ToMM(box.GetLeft()) < 0.4 or pcbnew.ToMM(box.GetRight()) > BOARD_W - 0.4
                        or pcbnew.ToMM(box.GetTop()) < 0.4 or pcbnew.ToMM(box.GetBottom()) > BOARD_H - 0.4):
                    fp.Remove(item)
    return board, nets


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
class Layout:
    """Board copper as the router sees it, plus the connectivity of every net."""

    def __init__(self, board, nets):
        self.board, self.nets = board, nets
        self.router = Router(BOARD_W, BOARD_H, {
            "sig": W_SIGNAL / 2 + CLEARANCE + 0.02,
            "fine": W_FINE / 2 + CLEARANCE + 0.02,
            "pwr": W_POWER / 2 + CLEARANCE + 0.02,
            "via": VIA_D / 2 + CLEARANCE + 0.015,
        }, n_layers=len(ALL))
        self.router.set_cost(I, Shape.rect(0, 0, BOARD_W, BOARD_H), INNER_COST)
        self.width = {"sig": W_SIGNAL, "fine": W_FINE, "pwr": W_POWER}
        self.comps = {}                     # net -> [{"cells": set((layer, x, y)), "plane": bool, "dim": mm}]
        self.segs, self.vias = [], []       # copper to emit: (net, layer, x0, y0, x1, y1, w), (net, x, y)
        self.routed = {}                    # net -> [(layers, shape)] added by the router (for rip-up)
        self.saved = None
        self.collect_pads()
        self.static_obstacles()

    # -- geometry ----------------------------------------------------------
    def collect_pads(self):
        r = self.router
        for fp in self.board.GetFootprints():
            ref = fp.GetReference()
            for pad in fp.Pads():
                box = pad.GetBoundingBox()
                x0, y0 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop())
                x1, y1 = pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())
                shape = Shape.rect(x0, y0, x1, y1)
                attr = pad.GetAttribute()
                if attr == pcbnew.PAD_ATTRIB_NPTH:
                    r.add(None, ALL, shape)
                    r.block_vias(shape, VIA_D / 2 + 0.25)
                    continue
                layers = tuple(l for l, k in KICAD_LAYER.items() if pad.IsOnLayer(k))
                net = short(pad.GetNetname()) if pad.GetNetname() else "nc:%s.%s" % (ref, pad.GetNumber())
                r.add(net, layers, shape)
                # No router via may overlap a pad (solder wicking); through-hole pads need hole spacing too.
                r.block_vias(shape, VIA_D / 2 + (0.25 if attr == pcbnew.PAD_ATTRIB_PTH else 0.05))
                if pad.GetNetname():
                    # Cells a track may end on must lie on the real copper, not in a rounded-off corner
                    # of the bounding box used above.
                    kind = pad.GetShape()
                    if kind == pcbnew.PAD_SHAPE_CIRCLE:
                        inside = Shape.circle((x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0) / 2).cells(0.05)
                    else:
                        corner = 0.0
                        if kind == pcbnew.PAD_SHAPE_OVAL:
                            corner = min(x1 - x0, y1 - y0) / 2
                        elif kind == pcbnew.PAD_SHAPE_ROUNDRECT:
                            corner = pcbnew.ToMM(pad.GetRoundRectCornerRadius())
                        inside = shape.cells(0.02 + 0.3 * corner)
                    cells = set((l, x, y) for l in layers for x, y in inside)
                    self.join(net, cells, plane=(attr == pcbnew.PAD_ATTRIB_PTH), dim=min(x1 - x0, y1 - y0))

    def static_obstacles(self):
        r = self.router
        nx, ny = r.nx, r.ny
        band = int(round((CORNER_R + 1.0) / PITCH))
        for cls, grow in r.classes.items():
            # copper edge to board edge >= EDGE_CLEARANCE; `grow` already holds half-width + CLEARANCE
            margin = grow - CLEARANCE + EDGE_CLEARANCE + 0.02
            for l in ALL:
                hard = r.hard[(l, cls)]
                for y in range(ny):
                    near_y = y < band or y >= ny - band
                    xs = range(nx) if near_y else list(range(band)) + list(range(nx - band, nx))
                    for x in xs:
                        px, py = x * PITCH, y * PITCH
                        d = min(px, BOARD_W - px, py, BOARD_H - py)
                        cx = CORNER_R if px < CORNER_R else (BOARD_W - CORNER_R if px > BOARD_W - CORNER_R else None)
                        cy = CORNER_R if py < CORNER_R else (BOARD_H - CORNER_R if py > BOARD_H - CORNER_R else None)
                        if cx is not None and cy is not None:
                            d = CORNER_R - math.hypot(px - cx, py - cy)
                        if d < margin:
                            hard[y * nx + x] = 1
        for (x0, y0, x1, y1) in KEEPOUT_F:
            r.block((F,), Shape.rect(x0, y0, x1, y1), 0.0, ("sig", "fine", "pwr"))
        for (x0, y0, x1, y1) in KEEPOUT_F[:2]:
            r.block_vias(Shape.rect(x0, y0, x1, y1), VIA_D / 2)

    # -- connectivity --------------------------------------------------------
    def join(self, net, cells, plane=False, dim=None):
        """Add copper cells to a net and merge every component they touch."""
        comps = self.comps.setdefault(net, [])
        merged = {"cells": set(cells), "plane": plane, "dim": dim}
        rest = []
        for c in comps:
            if not c["cells"].isdisjoint(merged["cells"]):
                merged["cells"] |= c["cells"]
                merged["plane"] = merged["plane"] or c["plane"]
                dims = [d for d in (merged["dim"], c["dim"]) if d is not None]
                merged["dim"] = min(dims) if dims else None
            else:
                rest.append(c)
        rest.append(merged)
        self.comps[net] = rest
        return merged

    # -- adding copper -------------------------------------------------------
    def add_track(self, net, layer, pts, w, routed=False):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            shape = Shape.segment(x0, y0, x1, y1, w)
            self.router.add(net, (layer,), shape)
            self.segs.append((net, layer, x0, y0, x1, y1, w))
            if routed:
                self.routed.setdefault(net, []).append(((layer,), shape))
            self.join(net, set((layer, x, y) for x, y in shape.cells(0.02)))

    def add_via(self, net, x, y, routed=False):
        shape = Shape.circle(x, y, VIA_D / 2)
        self.router.add(net, ALL, shape)
        self.router.block_vias(shape, VIA_D / 2 + 0.25 - CLEARANCE)   # hole-to-hole spacing, also same net
        self.vias.append((net, x, y))
        if routed:
            self.routed.setdefault(net, []).append((ALL, shape))
        self.join(net, set((l, cx, cy) for l in ALL for cx, cy in shape.cells(0.02)), plane=net in PLANE_NETS)

    def commit(self, net, cls, path, end_via=False):
        segs, vias = Router.path_to_tracks(path)
        for (l, x0, y0, x1, y1) in segs:
            self.add_track(net, l, [(x0, y0), (x1, y1)], self.width[cls], routed=True)
        if end_via:
            vias.append((path[-1][1] * PITCH, path[-1][2] * PITCH))
        for (x, y) in vias:
            self.add_via(net, x, y, routed=True)
        self.join(net, set(path))

    def snapshot(self):
        """Remember the state after the hand-made copper so a routing pass can be undone."""
        self.saved = ({n: [dict(c, cells=set(c["cells"])) for c in cs] for n, cs in self.comps.items()},
                      len(self.segs), len(self.vias), bytes(self.router.novia))

    def rip_up_all(self):
        comps, n_segs, n_vias, novia = self.saved
        for net, shapes in self.routed.items():
            self.router.remove_net_routes(net, shapes)
        self.routed = {}
        del self.segs[n_segs:]
        del self.vias[n_vias:]
        self.router.novia[:] = novia
        self.comps = {n: [dict(c, cells=set(c["cells"])) for c in cs] for n, cs in comps.items()}

    # -- strategies ------------------------------------------------------------
    @staticmethod
    def _bbox(comp):
        box = comp.get("bbox")
        if box is None or box[4] != len(comp["cells"]):
            xs = [c[1] for c in comp["cells"]]
            ys = [c[2] for c in comp["cells"]]
            box = comp["bbox"] = (min(xs), min(ys), max(xs), max(ys), len(comp["cells"]))
        return box

    def connect(self, net, max_cost=None, layers=None):
        """Join the components of a signal net with routed tracks. True when the net is one piece."""
        classes = ("pwr", "fine", "sig") if net in POWER_NETS else ("sig",)

        def gap(a, b):
            (ax0, ay0, ax1, ay1, _), (bx0, by0, bx1, by1, _) = self._bbox(a), self._bbox(b)
            return max(bx0 - ax1, ax0 - bx1, 0) + max(by0 - ay1, ay0 - by1, 0)

        progress = True
        while len(self.comps[net]) > 1 and progress:
            progress = False
            comps = sorted(self.comps[net], key=lambda c: len(c["cells"]))
            for comp in comps:
                # One target at a time, nearest first: keeps the A* heuristic tight.
                for target in sorted((c for c in comps if c is not comp), key=lambda c: gap(comp, c))[:3]:
                    for cls in classes:
                        path = self.router.find_path(net, comp["cells"], target["cells"], cls, max_cost=max_cost,
                                                     layers_allowed=layers)
                        if path:
                            self.commit(net, cls, path)
                            progress = True
                            break
                    if progress:
                        break
                if progress:
                    break
        return len(self.comps[net]) == 1

    def fan_out(self, net):
        """Give every pad group of a plane net a stub and a via. Returns the number left without one."""
        missing = 0
        while True:
            todo = [c for c in self.comps.get(net, []) if not c["plane"] and not c.get("failed")]
            if not todo:
                break
            comp = min(todo, key=lambda c: len(c["cells"]))
            layer = next(iter(comp["cells"]))[0]
            cls = "pwr" if (comp["dim"] or 0) >= 0.5 else "fine"
            targets = set()
            for c in self.comps[net]:
                if c["plane"]:
                    targets |= set(cell for cell in c["cells"] if cell[0] == layer)
            path = self.router.find_path(net, comp["cells"], targets, cls, allow_via=False, stop_at_via_site=True,
                                         layers_allowed=(layer,), max_nodes=80000)
            if path is None:
                comp["failed"] = True
                missing += 1
                continue
            self.commit(net, cls, path, end_via=path[-1] not in targets)
        return missing


def route_all(layout, verbose=True):
    tracks, vias = pre_routes()
    for (net, layer, w, pts) in tracks:
        layout.add_track(net, layer, pts, w)
    for (net, x, y) in vias:
        layout.add_via(net, x, y)
    layout.snapshot()

    signal_nets = [n for n in layout.comps if n not in PLANE_NETS and len(layout.comps[n]) > 1]

    def span(net):
        cells = [c for comp in layout.comps[net] for c in comp["cells"]]
        xs, ys = [c[1] for c in cells], [c[2] for c in cells]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    order = sorted(signal_nets, key=lambda n: (n in POWER_NETS, span(n)))
    failed = []
    for attempt in range(6):
        t0 = time.time()
        # The inner layer is only opened to signals if two passes on front + back did not finish.
        layers = (F, B) if attempt < 2 else ALL
        # 1. short local connections of every net, 2. +3V3 stubs, 3. everything else
        for net in order:
            layout.connect(net, max_cost=3.0 / PITCH * 10, layers=layers)
        miss33 = layout.fan_out("+3V3")
        failed = []
        for net in order:
            t1 = time.time()
            ok = layout.connect(net, layers=layers)
            if not ok:
                failed.append(net)
            if os.environ.get("PCB_VERBOSE"):
                print("    %-16s %s %.1f s" % (net, "ok" if ok else "FAILED", time.time() - t1), flush=True)
        if verbose:
            print("  pass %d (%d routing layers): %d nets, failed: %s, +3V3 pad groups without via: %d (%.0f s)"
                  % (attempt + 1, len(layers), len(order), failed or "none", miss33, time.time() - t0), flush=True)
        if not failed and not miss33:
            break
        if attempt == 5:
            return failed + (["+3V3"] if miss33 else [])
        layout.rip_up_all()
        order = failed + [n for n in order if n not in failed]
    missing_gnd = layout.fan_out("GND")
    if verbose:
        print("  GND pad groups without their own via (connected by the copper pours): %d" % missing_gnd)
    return []


def emit_copper(layout):
    board, nets = layout.board, layout.nets
    by_short = {short(n): item for n, item in nets.items()}
    for (net, l, x0, y0, x1, y1, w) in layout.segs:
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(x0, y0))
        t.SetEnd(pt(x1, y1))
        t.SetWidth(mm(w))
        t.SetLayer(KICAD_LAYER[l])
        t.SetNet(by_short[net])
        board.Add(t)
    for (net, x, y) in layout.vias:
        v = pcbnew.PCB_VIA(board)
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetPosition(pt(x, y))
        v.SetWidth(mm(VIA_D))
        v.SetDrill(mm(VIA_DRILL))
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(by_short[net])
        board.Add(v)


def add_zones(board, nets):
    inset = 0.0
    corners = [(inset, inset), (BOARD_W - inset, inset), (BOARD_W - inset, BOARD_H - inset), (inset, BOARD_H - inset)]
    for layer, net, prio in ((pcbnew.In1_Cu, "GND", 0), (pcbnew.In2_Cu, "+3V3", 0),
                             (pcbnew.F_Cu, "GND", 0), (pcbnew.B_Cu, "GND", 0)):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets[net])
        z.SetAssignedPriority(prio)
        z.SetLocalClearance(mm(0.2))
        z.SetMinThickness(mm(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        outline = z.Outline()
        outline.NewOutline()
        for (x, y) in corners:
            outline.Append(mm(x), mm(y))
        board.Add(z)


def add_silkscreen(board):
    def text(s, x, y, layer, size=0.8, mirror=False, angle=0):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(s)
        t.SetPosition(pt(x, y))
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(size * 0.15))
        t.SetMirrored(mirror)
        t.SetTextAngleDegrees(angle)
        board.Add(t)

    for ref, label in TP_LABEL.items():
        x, y, _rot, _side = PLACE[ref]
        text(label, x, y + 1.35, pcbnew.B_SilkS, 0.7, True)
    text("ACTIVITY BADGE  rev %s" % G.REV, BOARD_W / 2, 26.5, pcbnew.B_SilkS, 0.9, True)
    # Battery lead pads: J2 pad 1 (VBAT) is the one at x = 8, pad 2 (GND) at x = 12.
    jx, jy = PLACE["J2"][0], PLACE["J2"][1]
    text("BAT", jx, jy - 2.9, pcbnew.B_SilkS, 0.8, True)
    text("+", jx - 2.0, jy + 2.15, pcbnew.B_SilkS, 1.0, True)
    text("-", jx + 2.0, jy + 2.15, pcbnew.B_SilkS, 1.0, True)
    text("BOOT", 4.65, PLACE["SW1"][1], pcbnew.F_SilkS, 0.7, False, 90)
    text("RST", 15.35, PLACE["SW2"][1], pcbnew.F_SilkS, 0.7, False, 90)


def check_against_netlist(board):
    """Every pad of the saved board must carry the net the schematic gives it."""
    _stamps, pad_net = read_netlist()
    errors, seen = [], set()
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            key = (fp.GetReference(), pad.GetNumber())
            want = pad_net.get(key, "")
            if pad.GetNetname() != want:
                errors.append("%s.%s: board '%s', schematic '%s'" % (key[0], key[1], pad.GetNetname(), want))
            seen.add(key)
    errors += ["%s.%s missing on the board" % k for k in pad_net if k not in seen]
    return errors


def _mm(p):
    return (pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))


def _seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def prune_dangling(board):
    """
    Remove dead copper: vias that connect on one layer only and track ends that
    lead nowhere. They are left over when the router reached a pad without
    needing a hand-placed via. Connectivity is preserved: a via that served as
    a junction on its one layer is replaced by short track links, and a dangling
    track that other tracks branch off from is only shortened.
    Returns the number of items removed or shortened.
    """
    board.BuildConnectivity()
    connectivity = board.GetConnectivity()
    flagged = [t for t in board.GetTracks() if connectivity.TestTrackEndpointDangling(t, True)]
    changed = 0
    for item in flagged:
        net = item.GetNetCode()
        same_net = [t for t in board.GetTracks() if t.GetNetCode() == net and t.m_Uuid != item.m_Uuid]
        tracks = [t for t in same_net if t.GetClass() == "PCB_TRACK"]
        if item.GetClass() == "PCB_VIA":
            centre, radius = _mm(item.GetPosition()), pcbnew.ToMM(item.GetWidth()) / 2
            for t in tracks:
                for end in (t.GetStart(), t.GetEnd()):
                    d = math.dist(_mm(end), centre)
                    if 1e-4 < d <= radius:            # ended on the via's ring: link it to the centre
                        link = pcbnew.PCB_TRACK(board)
                        link.SetStart(end)
                        link.SetEnd(item.GetPosition())
                        link.SetWidth(t.GetWidth())
                        link.SetLayer(t.GetLayer())
                        link.SetNetCode(net)
                        board.Add(link)
            board.Remove(item)
            changed += 1
            continue

        layer, half = item.GetLayer(), pcbnew.ToMM(item.GetWidth()) / 2
        a, b = _mm(item.GetStart()), _mm(item.GetEnd())

        def attached(point):
            for pad in board.GetPads():
                if pad.GetNetCode() == net and pad.IsOnLayer(layer) and pad.HitTest(pcbnew.VECTOR2I(
                        pcbnew.FromMM(point[0]), pcbnew.FromMM(point[1]))):
                    return True
            for t in same_net:
                if t.GetClass() == "PCB_VIA":
                    if math.dist(point, _mm(t.GetPosition())) <= pcbnew.ToMM(t.GetWidth()) / 2:
                        return True
                elif t.GetLayer() == layer and _seg_dist(point, _mm(t.GetStart()), _mm(t.GetEnd())) <= pcbnew.ToMM(
                        t.GetWidth()) / 2:
                    return True
            return False

        a_ok, b_ok = attached(a), attached(b)
        if a_ok and b_ok:
            continue
        if not a_ok and not b_ok:
            board.Remove(item)
            changed += 1
            continue
        keep, loose = (a, b) if a_ok else (b, a)
        # other tracks that branch off this one: keep the part up to the farthest branch
        branches = [p for t in tracks if t.GetLayer() == layer for p in (_mm(t.GetStart()), _mm(t.GetEnd()))
                    if _seg_dist(p, a, b) <= half and math.dist(p, keep) > half]
        if not branches:
            board.Remove(item)
        else:
            far = max(branches, key=lambda p: math.dist(p, keep))
            length = math.dist(a, b)
            along = max(0.0, min(length, ((far[0] - keep[0]) * (loose[0] - keep[0]) +
                                          (far[1] - keep[1]) * (loose[1] - keep[1])) / length))
            if length - along < 1e-3:
                continue
            new_end = pcbnew.VECTOR2I(pcbnew.FromMM(keep[0] + (loose[0] - keep[0]) * along / length),
                                      pcbnew.FromMM(keep[1] + (loose[1] - keep[1]) * along / length))
            if a_ok:
                item.SetEnd(new_end)
            else:
                item.SetStart(new_end)
        changed += 1
    return changed


def run_drc(board):
    pcbnew.WriteDRCReport(board, DRC_PATH, pcbnew.EDA_UNITS_MILLIMETRES, True)
    counts, section = {}, None
    unconnected = 0
    for line in open(DRC_PATH):
        if line.startswith("** Found"):
            section = line
            if "unconnected" in line:
                unconnected = int(line.split()[2])
        elif line.startswith("["):
            kind = line[1:line.index("]")]
            counts[kind] = counts.get(kind, 0) + 1
    return counts, unconnected


def main():
    if "--prune-once" in sys.argv:
        # One clean-up step in a process of its own: pcbnew cannot reload a board within one process.
        board = pcbnew.LoadBoard(PCB_PATH)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        changed = prune_dangling(board)
        pcbnew.SaveBoard(PCB_PATH, board, True)
        print("pruned %d" % changed)
        return 0

    place_only = "--place-only" in sys.argv
    print("building board %.0f x %.0f mm" % (BOARD_W, BOARD_H))
    board, nets = build_board()
    if not place_only:
        layout = Layout(board, nets)
        print("routing")
        failed = route_all(layout)
        emit_copper(layout)
        if failed:
            print("UNROUTED: %s" % ", ".join(failed))
    add_zones(board, nets)
    add_silkscreen(board)
    pcbnew.SaveBoard(PCB_PATH, board, True)

    if not place_only and not os.environ.get("PCB_NO_PRUNE"):
        total = 0
        for _ in range(8):
            out = subprocess.run([sys.executable, os.path.abspath(__file__), "--prune-once"],
                                 capture_output=True, text=True).stdout
            changed = [int(line.split()[1]) for line in out.splitlines() if line.startswith("pruned ")]
            if not changed or changed[0] == 0:
                break
            total += changed[0]
        print("clean-up: %d unused via(s) / track end(s) removed" % total)

    # Reload through the project so the design rules of the .kicad_pro apply, then fill and check.
    board = pcbnew.LoadBoard(PCB_PATH)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(PCB_PATH, board, True)
    counts, unconnected = run_drc(board)
    mismatches = check_against_netlist(board)
    print("netlist: %s" % ("board matches the schematic netlist (%d pads)"
                           % sum(len(fp.Pads()) for fp in board.GetFootprints()) if not mismatches
                           else "MISMATCH\n  " + "\n  ".join(mismatches)))
    tracks = [t for t in board.GetTracks()]
    n_vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
    drills = sorted(set(pcbnew.ToMM(t.GetDrill()) for t in tracks if t.GetClass() == "PCB_VIA"))
    print("board: %d footprints, %d track segments, %d vias (drill %s mm)"
          % (len(board.GetFootprints()), len(tracks) - n_vias, n_vias, "/".join("%.2f" % d for d in drills)))
    print("DRC: %d unconnected, violations: %s" % (unconnected, counts or "none"))
    print("report: %s" % os.path.relpath(DRC_PATH, os.path.dirname(G.HW)))
    return 0 if (not counts and not unconnected and not mismatches) else 1


if __name__ == "__main__":
    sys.exit(main())
