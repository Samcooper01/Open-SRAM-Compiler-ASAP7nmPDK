"""Parse ngspice ASCII rawfiles (shared by the access-sim scripts)."""
import sys


def read_raw(path):
    lines = open(path).read().splitlines()
    nvars = 0
    names = []
    for i, ln in enumerate(lines):
        if ln.startswith("No. Variables"):
            nvars = int(ln.split(":")[1])
        elif ln.startswith("Variables:"):
            names = [lines[i + 1 + j].split()[1] for j in range(nvars)]
        elif ln.startswith("Values:"):
            toks = " ".join(lines[i + 1:]).split()
            pts, k = [], 0
            while k < len(toks):
                k += 1
                pts.append([float(toks[k + j]) for j in range(nvars)])
                k += nvars
            return names, pts
    return names, []


if __name__ == "__main__":
    for run in sys.argv[1:]:
        names, pts = read_raw(run)
        print(f"== {run}: {len(pts)} points, vars:")
        for j, n in enumerate(names):
            if n == "time":
                continue
            vals = [p[j] for p in pts]
            print(f"   {n:34s} final={vals[-1]*1000:7.1f} mV  "
                  f"min={min(vals)*1000:7.1f} max={max(vals)*1000:7.1f}")
