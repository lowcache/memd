#!/usr/bin/env python3
"""Replay recall-quality scenarios through memd's sync pipeline.

Use --samples N for independent repetitions and --jobs J for parallel samples.
Report per-probe pass counts, summed usage, and the effective curator models.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
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


def positive_integer(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def prepare_sample(memd, tmp, scenario, sample, cfg):
    name = scenario["name"]
    root = os.path.realpath(tmp / "projects" / name / f"s{sample}")
    tdir = tmp / "transcripts" / name / f"s{sample}"
    prepared = {"root": root, "tdir": tdir}
    cfg["projects"][root] = {
        "name": name,
        "extra_sources": [str(tdir / "*.jsonl")],
    }
    try:
        memd.scaffold(root, name)
        for fname, body in scenario.get("seed", {}).items():
            memd.write_memory_file(
                os.path.join(root, ".memory", fname), body, name, fname.split(".")[0]
            )
        prepared["seed_mem"] = memd.read_memory(root)
        tdir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        prepared["error"] = str(exc)
    return prepared


def run_sample(memd, scenario, sample, prepared):
    started = time.monotonic()
    name = scenario["name"]
    root = prepared["root"]
    tdir = prepared["tdir"]
    result = {
        "name": name,
        "sample": sample,
        "passed": False,
        "probes": [dict(probe, ok=False) for probe in scenario.get("probes", [])],
        "distills": 0,
        "distill_failures": 0,
        "usage": dict.fromkeys(USAGE_KEYS, 0),
        "seconds": 0,
    }
    try:
        if "error" in prepared:
            raise RuntimeError(prepared["error"])
        seed_mem = prepared["seed_mem"]
        overrides = scenario.get("config", {})
        for i, session in enumerate(scenario.get("sessions", [])):
            date = dt.datetime.fromisoformat(session["date"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=dt.timezone.utc)
            date = date.astimezone(dt.timezone.utc)
            with (tdir / f"s{i:02d}.jsonl").open("w", encoding="utf-8") as stream:
                for j, turn in enumerate(session.get("turns", [])):
                    role = turn["role"]
                    # claude-code's own shape: user text as a string, assistant
                    # text as content blocks
                    content = (turn["text"] if role == "user"
                               else [{"type": "text", "text": turn["text"]}])
                    timestamp = (date + dt.timedelta(minutes=j)).isoformat()
                    stream.write(json.dumps({
                        "type": role,
                        "message": {"role": role, "content": content},
                        "timestamp": timestamp.replace("+00:00", "Z"),
                        "cwd": root,
                        "sessionId": f"{name}-{i}",
                    }) + "\n")
            for note in session.get("inbox", []):
                memd.write_inbox_note(root, note, source="eval")
            ok = memd.sync_project(dict(memd.load_config(), **overrides), root,
                                   trigger="manual")
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


def aggregate_samples(scenario, samples):
    total = len(samples)
    passed = sum(sample["passed"] for sample in samples)
    probes = []
    for i, probe in enumerate(scenario.get("probes", [])):
        passes = sum(sample["probes"][i]["ok"] for sample in samples)
        probes.append(dict(probe, passes=passes, ok=passes == total))
    result = {
        "name": scenario["name"],
        "samples_total": total,
        "passed_samples": passed,
        "passed": passed == total,
        "samples": samples,
        "probes": probes,
        "distills": sum(sample["distills"] for sample in samples),
        "distill_failures": sum(sample["distill_failures"] for sample in samples),
        "usage": {key: sum(sample["usage"][key] for sample in samples)
                  for key in USAGE_KEYS},
        "seconds": sum(sample["seconds"] for sample in samples),
    }
    errors = [sample["error"] for sample in samples if "error" in sample]
    if errors:
        result["errors"] = errors
    return result


def print_scenario(result):
    passed = result["passed"]
    name = result["name"]
    probes = result["probes"]
    samples = result["samples_total"]
    ok = sum(probe["passes"] for probe in probes)
    n = len(probes) * samples
    distills = result["distills"]
    in_tok = result["usage"]["input_tokens"]
    out_tok = result["usage"]["output_tokens"]
    cost = result["usage"]["cost_usd"]
    print(f"{'PASS' if passed else 'FAIL'}  {name:28} "
          f"{result['passed_samples']}/{samples} samples  {ok}/{n} probe-runs  "
          f"{distills} distill(s)  {in_tok} in / {out_tok} out  ${cost:.4f}")
    if not passed:
        for probe in probes:
            if not probe["ok"]:
                kind = next((kind for kind in PROBE_KINDS if kind in probe), "unknown")
                print(f"  {kind}  {probe['file']}  {probe.get(kind, '')}  "
                      f"{probe['passes']}/{samples}")
        if result["distill_failures"]:
            print(f"  {result['distill_failures']} distill failure(s)")
        for error in result.get("errors", []):
            print(f"  error: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios-dir", type=Path, default=REPO / "eval" / "scenarios")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--samples", type=positive_integer, default=1,
                        help="independent samples per scenario (>= 1; default: 1)")
    parser.add_argument("--jobs", type=positive_integer, default=1,
                        help="parallel sample workers (>= 1; default: 1)")
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
               "antigravity_dir": str(tmp / "antigravity"),
               "git_commit": False, "min_digest_chars": 0}
        if args.curator_cmd is not None:
            cfg["curator_cmd"] = args.curator_cmd
        cfg.update(args.config)
        Path(memd.CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(memd.CONFIG_PATH).write_text(json.dumps(cfg), encoding="utf-8")
        cfg = memd.load_config()
        models = {key: cfg[key] for key in ("model_small", "model_large")}
        print(f"models: small={models['model_small']} large={models['model_large']}  "
              f"samples={args.samples}")
        prepared = [
            (scenario, sample, prepare_sample(memd, tmp, scenario, sample, cfg))
            for scenario in scenarios for sample in range(1, args.samples + 1)
        ]
        memd.save_config(cfg)
        if args.jobs > 1:
            with ThreadPoolExecutor(args.jobs) as executor:
                futures = [executor.submit(run_sample, memd, scenario, sample, context)
                           for scenario, sample, context in prepared]
                sample_results = [future.result() for future in futures]
        else:
            sample_results = [run_sample(memd, scenario, sample, context)
                              for scenario, sample, context in prepared]
        results = []
        for i, scenario in enumerate(scenarios):
            start = i * args.samples
            result = aggregate_samples(scenario, sample_results[start:start + args.samples])
            results.append(result)
            print_scenario(result)
        totals = {
            "scenarios_passed": sum(result["passed"] for result in results),
            "scenarios_total": len(results),
            "samples_passed": sum(r["passed_samples"] for r in results),
            "samples_total": sum(r["samples_total"] for r in results),
            "probes_passed": sum(probe["passes"] for r in results for probe in r["probes"]),
            "probes_total": sum(len(r["probes"]) * r["samples_total"] for r in results),
            **{key: sum(r["usage"][key] for r in results) for key in USAGE_KEYS},
        }
        output = {
            "run_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "curator": args.curator_cmd if args.curator_cmd is not None else "claude (default)",
            "config_overrides": args.config,
            "samples": args.samples,
            "models": models,
            "scenarios": results,
            "totals": totals,
        }
        print(f"TOTAL  {totals['scenarios_passed']}/{totals['scenarios_total']} scenarios  "
              f"{totals['samples_passed']}/{totals['samples_total']} samples  "
              f"{totals['probes_passed']}/{totals['probes_total']} probe-runs  "
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
