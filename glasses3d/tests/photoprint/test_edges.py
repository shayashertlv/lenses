"""Edge gate per (view, part) on FT1119 (WP5_DESIGN §3, D2): the port reproduces the probe's R P6 table."""
import pytest

from glasses3d.g3d_host.photoprint import edges as ED
from glasses3d.g3d_host.photoprint import inputs as IN

# WP5_DESIGN §3 (R P6), mean / p90 mm. The probe grouped each temple with its hinge pieces; the port labels parts
# individually, so temples are compared loosely and brows exactly.
DESIGN = {"01": {"Brow_Left": (0.71, 1.13), "Brow_Right": (0.70, 1.79), "Temple_Left": (0.68, 1.87), "Temple_Right": (0.96, 1.79)},
          "02": {"Brow_Left": (0.41, 0.79), "Brow_Right": (0.41, 0.88), "Temple_Left": (0.76, 1.59), "Temple_Right": (0.77, 1.63)},
          "03": {"Brow_Left": (2.20, 3.92), "Brow_Right": (2.70, 3.40), "Temple_Left": (0.58, 1.39), "Temple_Right": (3.27, 13.73)},
          "04": {"Brow_Left": (0.60, 1.33), "Brow_Right": (0.59, 1.34), "Temple_Left": (0.76, 1.58), "Temple_Right": (0.73, 1.50)},
          "05": {"Brow_Left": (0.35, 0.86), "Brow_Right": (0.35, 0.83), "Temple_Left": (0.83, 1.40), "Temple_Right": (1.04, 2.49)},
          "06": {"Brow_Left": (2.50, 4.31), "Brow_Right": (2.23, 3.81), "Temple_Left": (0.73, 1.69), "Temple_Right": (0.80, 1.71)}}


@pytest.fixture(scope="module")
def table(ft1119_job):
    return ED.edge_table(ft1119_job, ft1119_job.part_ids(IN.FT1119_PARTS))


def test_edges_reproduce_the_design_table(table):
    for v, parts in table.items():
        short = IN.short_name(v)
        for p, row in parts.items():
            mean, p90 = DESIGN[short][p]
            tol = 0.011 if p.startswith("Brow") else 0.07
            assert row["mean_mm"] == pytest.approx(mean, abs=tol), (v, p, row)
            assert row["p90_mm"] == pytest.approx(p90, abs=tol if p.startswith("Brow") else 0.1), (v, p, row)


def test_admission_follows_the_x_sign(table):
    """03 looks from -x: it may donate to the -x temple (Temple_Left) only; 06 to both temples, never the brows."""
    adm = ED.admitted(table)
    assert adm[("reference-03.png", "Temple_Left")] and not adm[("reference-03.png", "Temple_Right")]
    assert not adm[("reference-03.png", "Brow_Left")] and not adm[("reference-03.png", "Brow_Right")]
    assert adm[("reference-06.png", "Temple_Left")] and adm[("reference-06.png", "Temple_Right")]
    assert not adm[("reference-06.png", "Brow_Left")] and not adm[("reference-06.png", "Brow_Right")]
    assert all(adm[(v, p)] for v in ("reference-01.png", "reference-02.png", "reference-04.png")
               for p in IN.FT1119_PARTS)
