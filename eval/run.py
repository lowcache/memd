#!/usr/bin/env python3
"""Replay recall-quality scenarios through memd's sync pipeline."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time


REPO = Path(__file__).resolve().parent.parent
USAGE_KEYS = ("input_tokens", "output_tokens", "cost_usd")
PROBE_KINDS = ("must", "must_not", "unchanged", "preserve_headings")


def json_argument(value, expected):
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not isinstance(parsed, expected):
        raise argparse.ArgumentTypeError(f"expected a JSON {expected.__name__}")
    if expected is list and not all(isinstance(item, str) for item in parsed):
        raise argparse.ArgumentTypeError("curator command must contain strings")
    return parsed


def run_scenario(memd, tmp, scenario):
    started = time.monotonic()
    name = scenario["name"]
    root = os.path.realpath(tmp / "projects" / name)
    result = {
        "name": name,
        "passed": False,
        "probes": [dict(probe, ok=False) for probe in scenario.get("probes", [])],
        "distills": 0,
        "distill_failures": 0,
        "usage": dict.fromkeys(USAGE_KEYS, 0),
        "seconds": 0,
    }
    try:
        memd.scaffold(root, name)
        for fname, body in scenario.get("seed", {}).items():
            memd.write_memory_file(
                os.path.join(root, ".memory", fname), body, name, fname.split(".")[0]
            )
        seed_mem = memd.read_memory(root)
        tdir = tmp / "transcripts" / name
        tdir.mkdir(parents=True, exist_ok=True)
        cfg = memd.load_config()
        cfg["projects"][root] = {
            "name": name,
            "extra_sources": [str(tdir / "*.jsonl")],
        }
        memd.save_config(cfg)
        for i, session in enumerate(scenario.get("sessions", [])):
            date = dt.datetime.fromisoformat(session["date"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=dt.timezone.utc)
            date = date.astimezone(dt.timezone.utc)
            with (tdir / f"s{i:02d}.jsonl").open("w", encoding="utf-8") as stream:
                for j, turn in enumerate(session["turns"]):
                    role = turn["role"]
                    timestamp = (date + dt.timedelta(minutes=j)).isoformat()
                    stream.write(json.dumps({
                        "type": role,
                        "message": {"role": role, "content": turn["text"]},
                        "timestamp": timestamp.replace("+00:00", "Z"),
                        "cwd": root,
                        "sessionId": f"{name}-{i}",
                    }) + "\n")
            ok = memd.sync_project(memd.load_config(), root, trigger="manual")
            if ok is True:
                result["distills"] += 1
            elif ok is False:
                result["distill_failures"] += 1

        final_mem = memd.read_memory(root)
        archive = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((Path(root) / ".memory" / "archive").glob("*.md"))
        )
        texts = dict(final_mem, archive=archive)
        texts["*"] = "\n".join([*(final_mem[f] for f in memd.MEMORY_FILES), archive])
        for probe in result["probes"]:
            fname = probe["file"]
            text = texts[fname]
            if "must" in probe:
                ok = re.search(probe["must"], text) is not None
            elif "must_not" in probe:
                ok = re.search(probe["must_not"], text) is None
            elif "unchanged" in probe:
                ok = final_mem[fname].strip() == seed_mem[fname].strip()
            elif "preserve_headings" in probe:
                ok = all(
                    heading in final_mem[fname] or heading in archive
                    for heading, _ in memd.split_sections(seed_mem[fname])
                )
            else:
                raise ValueError(f"unknown probe: {probe}")
            probe["ok"] = bool(ok)
        result["passed"] = (
            all(probe["ok"] for probe in result["probes"])
            and result["distill_failures"] == 0
        )
    except Exception as exc:
        result["error"] = str(exc)
    try:
        history = memd.load_json(memd.META_PATH, {}).get(root, {}).get("history", [])
        for entry in history:
            for key in USAGE_KEYS:
                result["usage"][key] += entry.get("usage", {}).get(key, 0)
    except Exception as exc:
        result["passed"] = False
        result["error"] = str(exc)
    result["seconds"] = time.monotonic() - started
    return result


def print_scenario(result):
    passed = result["passed"]
    name = result["name"]
    probes = result["probes"]
    ok = sum(probe["ok"] for probe in probes)
    n = len(probes)
    distills = result["distills"]
    in_tok = result["usage"]["input_tokens"]
    out_tok = result["usage"]["output_tokens"]
    cost = result["usage"]["cost_usd"]
    print(f"{'PASS' if passed else 'FAIL'}  {name:28} {ok}/{n} probes  "
          f"{distills} distill(s)  {in_tok} in / {out_tok} out  ${cost:.4f}")
    if not passed:
        for probe in probes:
            if not probe["ok"]:
                kind = next((kind for kind in PROBE_KINDS if kind in probe), "unknown")
                print(f"  {kind}  {probe['file']}  {probe.get(kind, '')}")
        if result["distill_failures"]:
            print(f"  {result['distill_failures']} distill failure(s)")
        if "error" in result:
            print(f"  error: {result['error']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios-dir", type=Path, default=REPO / "eval" / "scenarios")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--curator-cmd", type=lambda value: json_argument(value, list))
    parser.add_argument("--config", type=lambda value: json_argument(value, dict), default={})
    parser.add_argument("--out", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    try:
        scenarios = []
        for path in sorted(args.scenarios_dir.iterdir()):
            if path.suffix == ".json":
                scenario = json.loads(path.read_text(encoding="utf-8"))
                if not args.scenario or scenario["name"] in args.scenario:
                    scenarios.append(scenario)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"cannot read scenarios: {exc}")
    if not scenarios:
        parser.error("no scenario matched")

    tmp = Path(tempfile.mkdtemp(prefix="memd-eval-"))
    try:
        for variable, directory in (("XDG_CONFIG_HOME", "xdg-config"),
                                    ("XDG_STATE_HOME", "xdg-state")):
            path = tmp / directory
            path.mkdir()
            os.environ[variable] = str(path)
        sys.path.insert(0, str(REPO))
        import memd

        cfg = {"global_root": str(tmp / "ghome"), "auto_scaffold": False,
               "git_commit": False, "min_digest_chars": 0}
        if args.curator_cmd is not None:
            cfg["curator_cmd"] = args.curator_cmd
        cfg.update(args.config)
        Path(memd.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(memd.CONFIG_PATH).write_text(json.dumps(cfg), encoding="utf-8")
        results = []
        for scenario in scenarios:
            result = run_scenario(memd, tmp, scenario)
            results.append(result)
            print_scenario(result)
        totals = {
            "scenarios_passed": sum(result["passed"] for result in results),
            "scenarios_total": len(results),
            "probes_passed": sum(probe["ok"] for r in results for probe in r["probes"]),
            "probes_total": sum(len(r["probes"]) for r in results),
            **{key: sum(r["usage"][key] for r in results) for key in USAGE_KEYS},
        }
        output = {
            "run_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "curator": args.curator_cmd if args.curator_cmd is not None else "claude (default)",
            "config_overrides": args.config,
            "scenarios": results,
            "totals": totals,
        }
        print(f"TOTAL  {totals['scenarios_passed']}/{totals['scenarios_total']} scenarios  "
              f"{totals['probes_passed']}/{totals['probes_total']} probes  "
              f"{totals['input_tokens']} in / {totals['output_tokens']} out  "
              f"${totals['cost_usd']:.4f}")
        if args.out:
            args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        return 0 if all(result["passed"] for result in results) else 1
    finally:
        if args.keep:
            print(f"Kept temp tree: {tmp}")
        else:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    raise SystemExit(main())
