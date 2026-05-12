from __future__ import annotations
import json, re, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = json.loads((BASE/"data/current_index.json").read_text(encoding="utf-8"))

def valid_h(v):
    try: h=int(float(v))
    except Exception: return False
    return 145 <= h <= 215

def valid_b(v):
    m=re.search(r"(19\d{2}|20\d{2})", str(v or ""))
    return bool(m and 1980 <= int(m.group(1)) <= 2011)

def pos(p):
    v=str(p.get("display_position") or p.get("position") or p.get("position_role") or "").strip()
    return bool(v and v not in {"-", "기타", "UNK", "수집중", "검증필요", "공식확인 대기"})

ok=True
for comp in ["K리그1","K리그2"]:
    rows=[p for p in DATA.get("players",[]) if p.get("competition")==comp]
    missing=[p for p in rows if not (pos(p) and valid_h(p.get("height_cm")) and valid_b(p.get("birthdate")) and p.get("display_profile"))]
    print(f"{comp}: total={len(rows)}, missing={len(missing)}")
    if len(rows)!=30 or missing:
        ok=False
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 2)
