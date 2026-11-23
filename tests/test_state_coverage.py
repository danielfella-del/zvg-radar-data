#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))

path=Path(__file__).resolve().parents[1]/"collector"/"collect.py"
spec=importlib.util.spec_from_file_location("collect",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
expected={"bw","by","be","br","hb","hh","he","mv","ni","nw","rp","sl","sn","st","sh","th"}
assert set(mod.STATES)==expected, f"Bundesländer unvollständig: {sorted(expected-set(mod.STATES))}"
print("16 Bundesländer im Collector vorhanden.")
