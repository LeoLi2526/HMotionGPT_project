"""Offline receipt timeline; callers must confirm the same host and run.

This module does not establish sample synchronization, source integrity, or
continuous coverage between receipts. All original CSV values and row order are
retained. No device access, resampling, clock fitting, or device-time arithmetic.
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path


ADDED_FIELDS = ("host_relative_seconds", "in_overlap")
TIME_FIELDS = {"arm": "host_receive_completion_monotonic_ns",
               "ring": "host_monotonic_ns"}


def _seconds(ns):
    # Subtract integer nanoseconds before conversion, including for CSV output.
    sign = "-" if ns < 0 else ""
    whole, fraction = divmod(abs(ns), 1_000_000_000)
    return f"{sign}{whole}.{fraction:09d}"


def _span(times):
    if not times:
        return {"start_ns": None, "end_ns": None, "seconds": 0.0}
    start, end = min(times), max(times)
    return {"start_ns": start, "end_ns": end, "seconds": (end - start) / 1e9}


def _realtime(row, stream):
    # Existing arm derive has no replay/context columns: it describes serial
    # read completions. If supplied, explicit contrary provenance still wins.
    if row.get("replay", "0" if stream == "arm" else "") != "0":
        return False
    context = row.get("context", "live" if stream == "arm" else "")
    allowed = ("live", "ble") if stream == "ring" else ("live", "serial")
    if context not in allowed:
        return False
    return row.get("attribution", "current_capture_unverified") == "current_capture_unverified"


def _read(path, stream):
    issues = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        fields = reader.fieldnames
        if not fields or len(set(fields)) != len(fields) or any(not f for f in fields):
            raise ValueError(f"{stream}: CSV must have unique, nonempty column names")
        if set(fields).intersection(ADDED_FIELDS):
            raise ValueError(f"{stream}: derived column already exists")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError(f"{stream}: CSV row width differs from header")
    field = TIME_FIELDS[stream]
    if field not in fields:
        issues.append({"code": "missing_host_time_column", "rows": []})
    times, live = [], []
    bad = {"missing_host_time": [], "invalid_host_time": [],
           "host_time_regression": [], "realtime_host_time_regression": []}
    previous = previous_live = None
    for number, row in enumerate(rows, 1):
        raw = row.get(field, "")
        time = int(raw) if re.fullmatch(r"[0-9]+", raw) else None
        realtime = _realtime(row, stream)
        times.append(time)
        live.append(realtime)
        if time is None:
            bad["missing_host_time" if not raw.strip() else "invalid_host_time"].append(number)
            continue
        if previous is not None and time < previous:
            bad["host_time_regression"].append(number)
        previous = time
        if realtime:
            if previous_live is not None and time < previous_live:
                bad["realtime_host_time_regression"].append(number)
            previous_live = time
    issues.extend({"code": code, "rows": numbers} for code, numbers in bad.items() if numbers)
    # A host clock regression cannot be repaired by sorting or device epochs.
    # Keep observations, but withhold overlap for this entire realtime stream.
    regression = bool(bad["realtime_host_time_regression"])
    eligible = [t is not None and is_live and not regression for t, is_live in zip(times, live)]
    counts = Counter(t for t in times if t is not None)
    repeated = {t: n for t, n in counts.items() if n > 1}
    realtime_times = sorted({t for t, is_live in zip(times, live) if t is not None and is_live})
    max_gap = (max((b - a for a, b in zip(realtime_times, realtime_times[1:])), default=0) / 1e9
               if realtime_times else None)
    replay_rows = [i for i, row in enumerate(rows, 1) if row.get("replay") == "1"]
    nonlive_rows = [i for i, is_live in enumerate(live, 1) if not is_live]
    if replay_rows:
        issues.append({"code": "replay_excluded", "rows": replay_rows})
    if nonlive_rows:
        issues.append({"code": "non_realtime_excluded", "rows": nonlive_rows})
    if max_gap is not None and max_gap > 2.0:
        issues.append({"code": "receive_gap_exceeds_engineering_threshold", "rows": [],
                       "max_receive_gap_seconds": max_gap, "threshold_seconds": 2.0})
    summary = {
        "rows": len(rows), "rows_with_host_time": sum(t is not None for t in times),
        "missing_host_time_rows": len(bad["missing_host_time"]),
        "invalid_host_time_rows": len(bad["invalid_host_time"]),
        "non_realtime_rows": live.count(False),
        "overlap_eligible_rows": sum(eligible), "rows_in_overlap": 0,
        "receive_span": _span([t for t in times if t is not None]),
        "eligible_receive_span": _span([t for t, ok in zip(times, eligible) if ok]),
        "overlap_withheld_for_regression": regression,
        "max_receive_gap_seconds": max_gap,
        "receive_gap_engineering_pass": max_gap <= 2.0 if max_gap is not None else None,
        "receive_gap_semantics": "Largest gap between sorted distinct valid realtime host receipt times; "
                                 "replay/non-live excluded; null if none, zero if only one. "
                                 "Sorting for this statistic does not repair a reported regression.",
        "duplicate_receive_times": {
            "timestamp_groups": len(repeated), "rows": sum(repeated.values()),
            "extra_rows": sum(n - 1 for n in repeated.values()),
            "max_rows_per_timestamp": max(counts.values(), default=0),
            "interpretation": "Equal receipt times are retained; they do not prove duplicate samples or retransmission. "
                              "Ring batch samples share a receipt time; arm rows may share a read completion or clock tick.",
        },
        "quality_issues": issues,
    }
    summary["counts"] = {key: summary[key] for key in (
        "rows", "rows_with_host_time", "missing_host_time_rows", "invalid_host_time_rows",
        "non_realtime_rows", "overlap_eligible_rows", "rows_in_overlap")}
    summary["counts"].update(replay_rows=len(replay_rows),
                             distinct_realtime_receive_times=len(realtime_times),
                             host_time_regression_rows=len(bad["host_time_regression"]))
    return fields, rows, times, eligible, summary


def _ring_groups(rows, times, eligible):
    groups = {}
    for row, time, ok in zip(rows, times, eligible):
        key = (row.get("capture_id", ""), row.get("time_epoch", ""))
        group = groups.setdefault(key, {"rows": 0, "times": [], "eligible": [], "batches": set()})
        group["rows"] += 1
        if time is not None:
            group["times"].append(time)
        if ok:
            group["eligible"].append(time)
        # Sequence is local to capture_id; receipt distinguishes repeated delivery.
        group["batches"].add((row.get("sequence", ""), row.get("receipt_index", "")))
    return [{"capture_id": capture or None, "time_epoch": epoch or None,
             "rows": g["rows"], "batch_receipt_groups": len(g["batches"]),
             "receive_span": _span(g["times"]),
             "eligible_receive_span": _span(g["eligible"])}
            for (capture, epoch), g in groups.items()]


def build_timeline(arm_csv: Path, ring_csv: Path, output: Path, origin_ns: int) -> dict:
    """Create a new output directory containing two receipt CSVs and report.json.

    The caller must confirm inputs belong to the same host and run. Missing or
    invalid times remain blank in the relative column and produce quality issues.
    Replay/non-live rows retain their observed relative receipt time, but never
    enter overlap. A realtime host regression disables that stream's overlap.
    Overlap is the intersection of eligible receipt envelopes, not measured
    continuous coverage; gaps and device generations are not clock-corrected.
    Touching envelopes have zero duration and all in_overlap values are 0.
    Existing output paths (even empty directories) are always refused.
    """
    if isinstance(origin_ns, bool) or not isinstance(origin_ns, int) or origin_ns < 0:
        raise ValueError("origin_ns must be a nonnegative integer monotonic timestamp")
    arm_csv, ring_csv, output = Path(arm_csv), Path(ring_csv), Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")
    streams = {"arm": _read(arm_csv, "arm"), "ring": _read(ring_csv, "ring")}
    spans = [data[4]["eligible_receive_span"] for data in streams.values()]
    start = end = None
    if all(s["start_ns"] is not None for s in spans):
        candidate_start = max(s["start_ns"] for s in spans)
        candidate_end = min(s["end_ns"] for s in spans)
        if candidate_end > candidate_start:
            start, end = candidate_start, candidate_end
    ring = streams["ring"]
    groups = _ring_groups(ring[1], ring[2], ring[3])
    report = {
        "schema_version": 1, "origin_ns": origin_ns,
        "sources": {"arm_csv": str(arm_csv.resolve()), "ring_csv": str(ring_csv.resolve())},
        "synchronization": "unverified", "timing_error": "unknown",
        "host_clock_scope": "same host and same run; provenance must be confirmed by caller",
        "source_provenance_verified": False,
        "time_fields": TIME_FIELDS.copy(),
        "overlap_seconds": (end - start) / 1e9 if start is not None else 0.0,
        "overlap_start_ns": start, "overlap_end_ns": end,
        "overlap_semantics": "Intersection of eligible host receipt min/max envelopes, endpoints inclusive for positive duration. "
                             "Not continuous coverage, sample synchronization, or a device sampling interval.",
        "device_time_semantics": "All device time fields preserved verbatim; unused for receipt arithmetic. "
                                 "No interpolation or expansion of ring batch receipt times.",
        "ring_capture_ids": list(dict.fromkeys(g["capture_id"] for g in groups)),
        "ring_capture_epochs": groups,
        "streams": {name: data[4] for name, data in streams.items()},
        "quality_issues": [],
        "engineering_checks": {
            "minimum_overlap_fraction_of_requested_duration": 0.8,
            "overlap_check": "caller must compare overlap_seconds >= requested_duration_seconds * 0.8",
            "max_receive_gap_threshold_seconds": 2.0,
            "receive_gap_failed_streams": [name for name, data in streams.items()
                                           if data[4]["receive_gap_engineering_pass"] is False],
            "interpretation": "Engineering coverage thresholds only, not synchronization accuracy. "
                              "No overall capture verdict: requested duration, child exit status, and raw QC are caller responsibilities.",
        },
        "qc_flags": ["synchronization_unverified", "timing_error_unknown"],
    }
    for name, data in streams.items():
        report["qc_flags"].extend(f"{name}:{issue['code']}" for issue in data[4]["quality_issues"])
        report["quality_issues"].extend({"stream": name, **issue} for issue in data[4]["quality_issues"])
    if any(g["capture_id"] is None or g["time_epoch"] is None for g in groups):
        report["qc_flags"].append("ring:missing_capture_or_epoch")
        report["quality_issues"].append({"stream": "ring", "code": "missing_capture_or_epoch", "rows": []})
    for code, condition in (
        ("multiple_capture_generations", len({g["capture_id"] for g in groups if g["capture_id"] is not None}) > 1),
        ("multiple_capture_epochs", len(groups) > 1),
    ):
        if condition:
            report["qc_flags"].append(f"ring:{code}")
            report["quality_issues"].append({"stream": "ring", "code": code, "rows": [],
                                             "groups": [{"capture_id": g["capture_id"], "time_epoch": g["time_epoch"]}
                                                        for g in groups]})
    output.mkdir(parents=True, exist_ok=False)
    for name, (fields, rows, times, eligible, summary) in streams.items():
        with (output / f"{name}_receive.csv").open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields + list(ADDED_FIELDS))
            writer.writeheader()
            for row, time, ok in zip(rows, times, eligible):
                inside = bool(ok and start is not None and start <= time <= end)
                summary["rows_in_overlap"] += int(inside)
                writer.writerow({**row, "host_relative_seconds": _seconds(time - origin_ns) if time is not None else "",
                                 "in_overlap": int(inside)})
        summary["counts"]["rows_in_overlap"] = summary["rows_in_overlap"]
    with (output / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return report
