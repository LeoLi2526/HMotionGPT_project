"""Offline, single-clock affine calibration from device-observable shared events.

Fit anchors estimate target = a * source + b; independent check anchors
are required for verification; fit residuals must also satisfy the threshold.
Event IDs must identify physical events consistently: the
tool cannot detect the same event relabelled with different IDs. A host recording
button is not hardware synchronization evidence. Verification is limited to the
supplied events for this one clock map, never whole-episode or three-modal sync.

CLI example (the threshold is an operator choice, not a project default):
    python scripts/fit_clock_map.py anchors.json --output map.json --max-error-ms 5

Exit codes: 0 = report written (inspect its verification status), 2 = invalid
input or output failure. Inputs/raw streams are read-only; output must be new.
"""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys


ALGORITHM = "affine_ols_fit_only_centered_v1"
SCOPE = (
    "Only the supplied independent events for this single clock map are checked. "
    "This does not establish whole-episode or three-modal synchronization. "
    "Use matching events observed in the devices; a host recording button is "
    "not hardware synchronization evidence. Event identity and provenance "
    "must be established by the operator."
)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is outside the supported numeric range") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _finite_json(value):
    """Check even extra evidence fields, without dropping or coercing them."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _number(value, "input evidence number")
    elif value is not None and not isinstance(value, (str, bool)):
        raise ValueError("input evidence must contain only JSON values")


def fit_clock_map(document, max_error_ms):
    """Return a report without modifying input; threshold is always required.

    All weights are equal, and only role='fit' anchors influence coefficients.
    Nonpositive scale is rejected because clock time must advance. Numeric
    overflow/underflow producing a degenerate fit is rejected, never verified.
    """
    threshold = _number(max_error_ms, "max_error_ms")
    if threshold < 0:
        raise ValueError("max_error_ms must be nonnegative")
    if not isinstance(document, dict):
        raise ValueError("input must be a JSON object")
    _finite_json(document)
    modality = _text(document.get("modality"), "modality")
    source_clock = _text(document.get("source_clock"), "source_clock")
    if document.get("source_unit") != "seconds":
        raise ValueError("source_unit must be 'seconds'")
    if document.get("target_clock") != "episode_relative_seconds":
        raise ValueError("target_clock must be 'episode_relative_seconds'")
    anchors = document.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("anchors must be a list")
    seen = set()
    rows = []
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            raise ValueError(f"anchors[{index}] must be an object")
        event_id = _text(anchor.get("event_id"), f"anchors[{index}].event_id")
        # Whitespace variants must not allow an event to leak into check data.
        identity = event_id.strip()
        if identity in seen:
            raise ValueError(f"duplicate event_id (including fit/check overlap): {event_id}")
        seen.add(identity)
        role = anchor.get("role")
        if role not in ("fit", "check"):
            raise ValueError(f"anchors[{index}].role must be 'fit' or 'check'")
        x = _number(anchor.get("source_seconds"), f"anchors[{index}].source_seconds")
        y = _number(anchor.get("target_seconds"), f"anchors[{index}].target_seconds")
        rows.append((event_id, role, x, y))
    fit = [row for row in rows if row[1] == "fit"]
    if len(fit) < 2:
        raise ValueError("at least two fit anchors at distinct source times are required")
    lower = min(row[2] for row in fit)
    upper = max(row[2] for row in fit)
    if lower == upper:
        raise ValueError("fit source times have zero variance")

    # Subtract a reference before centering to retain precision at epoch times.
    x0, y0 = fit[0][2:]
    try:
        dx = [_number(row[2] - x0, "source difference") for row in fit]
        dy = [_number(row[3] - y0, "target difference") for row in fit]
        mx = math.fsum(v / len(fit) for v in dx)
        my = math.fsum(v / len(fit) for v in dy)
        xx = math.fsum((v - mx) ** 2 for v in dx)
        if not math.isfinite(xx) or xx <= 0:
            raise ValueError("fit variance is zero or outside supported numeric range")
        xy = math.fsum((x - mx) * (y - my) for x, y in zip(dx, dy))
        a = _number(xy / xx, "scale")
        if a <= 0:
            raise ValueError("scale must be positive")
        offset = _number(y0 + my - a * mx, "reference target")
        b = _number(offset - a * x0, "offset b")
        residuals = []
        for event_id, role, x, y in rows:
            predicted = _number(a * (x - x0) + offset, "predicted target")
            residual = _number((predicted - y) * 1000.0, "residual_ms")
            residuals.append({
                "event_id": event_id, "role": role,
                "source_seconds": x, "target_seconds": y,
                "predicted_target_seconds": predicted,
                "residual_ms": residual, "absolute_error_ms": abs(residual),
                "extrapolation": x < lower or x > upper,
            })
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("fit exceeds supported numeric range") from exc

    checks = [r for r in residuals if r["role"] == "check"]
    max_check_error = max((r["absolute_error_ms"] for r in checks), default=None)
    max_fit_error = max(r["absolute_error_ms"] for r in residuals if r["role"] == "fit")
    extrapolated = [r["event_id"] for r in checks if r["extrapolation"]]
    reasons = []
    if not checks:
        reasons.append("no_check_events")
    if max_check_error is not None and max_check_error > threshold:
        reasons.append("check_error_exceeds_threshold")
    if max_fit_error > threshold:
        reasons.append("fit_error_exceeds_threshold")
    if extrapolated:
        reasons.append("check_extrapolation")
    verified = not reasons
    return {
        "schema_version": 1,
        "algorithm": ALGORITHM,
        "modality": modality, "source_clock": source_clock,
        "source_unit": "seconds", "target_clock": "episode_relative_seconds",
        "mapping": {
            "formula": "target_seconds = a * source_seconds + b",
            "a": a, "b": b,
            "evaluation_formula": "a * (source_seconds - source_reference_seconds) + target_at_reference_seconds",
            "source_reference_seconds": x0, "target_at_reference_seconds": offset,
        },
        "fit_source_range_seconds": [lower, upper],
        "fit_count": len(fit), "check_count": len(checks),
        "max_error_ms": threshold,
        "max_check_absolute_error_ms": max_check_error,
        "max_fit_absolute_error_ms": max_fit_error,
        "extrapolation": bool(extrapolated),
        "extrapolated_check_event_ids": extrapolated,
        "verified_for_supplied_events": verified,
        "status": "verified_for_supplied_events" if verified else "not_verified",
        "failure_reasons": reasons,
        "verification_scope": SCOPE,
        "residual_sign": "predicted_target_minus_observed_target",
        "residuals": residuals,
        "input_evidence": copy.deepcopy(document),
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON of matched device synchronization events")
    parser.add_argument("--output", required=True, type=Path, help="new report JSON; never overwrite")
    parser.add_argument("--max-error-ms", required=True, type=float,
                        help="operator-supplied maximum absolute fit and check error in ms (no default)")
    args = parser.parse_args(argv)
    try:
        raw = args.input.read_bytes()
        source_text = raw.decode("utf-8-sig")
        document = json.loads(source_text, object_pairs_hook=_unique_object)
        report = fit_clock_map(document, args.max_error_ms)
        report["input_file"] = {
            "path": str(args.input.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "text": raw.decode("utf-8"),
        }
        # Serialize before creation, then use exclusive creation (also protects
        # the input itself and existing links/aliases from accidental overwrite).
        encoded = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
    except (OSError, ValueError, OverflowError) as exc:
        print(f"fit_clock_map: {exc}", file=sys.stderr)
        return 2
    print(f"{report['status']}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
