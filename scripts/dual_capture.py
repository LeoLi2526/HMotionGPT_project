#!/usr/bin/env python3
"""One-host engineering capture. Reception overlap is not sample synchronization."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import experiment_session as session


def inspect_episode(episode, output):
    report = session.status(argparse.Namespace(episode=episode))
    session.write_new_json(output, report)
    return report


def process_episode(episode, arm_id, ring_id, derive_id, origin_ns):
    """Decode originals, retaining partial reports even when one stream fails."""
    from dual_timeline import build_timeline
    episode = session.require_episode(episode)
    out = episode / 'dual_derived' / session.identifier(derive_id)
    out.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 1, 'synchronization': 'unverified',
              'timing_error_ms': None, 'errors': [], 'timeline': None}
    for name, handler, capture_id in [('armband', session.derive, arm_id),
                                       ('ring', session.ring_derive, ring_id)]:
        try:
            result[name] = handler(argparse.Namespace(episode=episode,
                capture_id=capture_id, derive_id=derive_id, include_replay=False))
            if name == 'armband':
                invalid = session.read_json(episode / 'derived' / derive_id / 'invalid_lines.json')
                boundary = sum(row.get('raw_offset') == 0 and row.get('row_index') == 0 for row in invalid)
                result[name]['start_boundary_invalid_lines'] = boundary
                result[name]['interior_invalid_line_count'] = len(invalid) - boundary
        except Exception as exc:
            result['errors'].append(f'{name}: {type(exc).__name__}: {exc}')
    try:
        result['integrity'] = session.status(argparse.Namespace(episode=episode))
        if 'armband' in result and 'ring' in result:
            result['timeline'] = build_timeline(episode / 'derived' / derive_id / 'rows.csv',
                episode / 'ring_derived' / derive_id / 'imu.csv', out / 'timeline', origin_ns)
    except Exception as exc:
        result['errors'].append(f'processing: {type(exc).__name__}: {exc}')
    session.write_new_json(out / 'processing.json', result)
    return result


def supervise(commands, directory, timeout):
    """Own only these child processes; save exit status on failure or cancellation."""
    children, streams, errors, codes = {}, [], [], {}
    deadline = time.monotonic() + timeout
    try:
        for name, command in commands.items():
            out = (directory / f'{name}.stdout.log').open('x', encoding='utf-8')
            err = (directory / f'{name}.stderr.log').open('x', encoding='utf-8')
            streams.extend([out, err])
            children[name] = subprocess.Popen(command, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        while any(child.poll() is None for child in children.values()):
            failed = [name for name, child in children.items() if child.poll() not in (None, 0)]
            if failed:
                raise RuntimeError('capture failed: ' + ', '.join(failed))
            if time.monotonic() > deadline:
                raise TimeoutError('dual capture deadline exceeded')
            time.sleep(0.1)
    except (Exception, KeyboardInterrupt) as exc:
        errors.append(f'{type(exc).__name__}: {exc}')
    finally:
        previous = {}
        # Repeated Ctrl+C must not abandon the second owned worker during cleanup.
        for sig in [signal.SIGINT] + ([signal.SIGBREAK] if os.name == 'nt' else []):
            previous[sig] = signal.signal(sig, signal.SIG_IGN)
        # CTRL_BREAK is handled by our own worker; then use bounded force termination.
        try:
            for name, child in children.items():
                if child.poll() is None:
                    try:
                        child.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
                        child.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
                        try:
                            child.terminate()
                            child.wait(timeout=5)
                        except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
                            child.kill()
                            child.wait(timeout=5)
                        errors.append(f'{name}: forced termination; raw data may be incomplete')
                codes[name] = child.returncode
        finally:
            for stream in streams:
                stream.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return {'exit_codes': codes, 'errors': errors,
            'completed': len(codes) == len(commands) and all(c == 0 for c in codes.values()) and not errors}


def quality_issues(processing, requested_seconds):
    issues = list(processing.get('errors', []))
    arm, ring = processing.get('armband', {}), processing.get('ring', {})
    # Derive returns the QC report, not a sample-synchronization certificate.
    if not arm.get('valid_rows') or set(map(str, arm.get('column_width_counts', {}))) != {'8'}:
        issues.append('armband must contain eight-column rows only')
    if arm.get('interior_invalid_line_count', arm.get('invalid_line_count', 0)):
        issues.append('armband has invalid complete lines beyond the unverified starting boundary')
    if not ring.get('raw_verified') or not ring.get('live_imu_samples') or ring.get('exit_code') != 0:
        issues.append('ring integrity/live capture check failed')
    if ring.get('invalid_frames', 0) or ring.get('conflicting_keys') or ring.get('time_epoch_boundaries'):
        issues.append('ring invalid frames, conflicts or clock epoch changes')
    fatal_ring_flags = {'conflicting_sample_config', 'missing_or_conflicting_sample_config',
                        'device_reported_gap', 'device_time_backwards', 'nominal_sample_time_overlap',
                        'config_period_mismatch', 'batch_timestamp_mismatch'}
    issues.extend('ring: ' + flag for flag in ring.get('qc_flags', []) if flag in fatal_ring_flags)
    arm_records = processing.get('integrity', {}).get('captures', [])
    if not arm_records or not all(r.get('raw_verified') and r.get('exit_code') == 0 for r in arm_records):
        issues.append('armband raw integrity/capture check failed')
    timeline = processing.get('timeline')
    if timeline is None:
        issues.append('receive timeline unavailable')
    else:
        issues.extend(timeline.get('quality_issues', []))
        if timeline.get('overlap_seconds', 0) < requested_seconds * 0.8:
            issues.append('receive overlap below 80 percent of requested duration')
    return issues


def record(args):
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 3600:
        raise ValueError('seconds must be between 1 and 3600')
    if not math.isfinite(args.startup_allowance) or not 5 <= args.startup_allowance <= 120:
        raise ValueError('startup-allowance must be between 5 and 120 seconds')
    if not args.address.strip():
        raise ValueError('explicit ring address required')
    args.purpose = 'engineering'
    created = session.create(args)
    episode = Path(created['episode'])
    directory = episode / 'dual_run'
    directory.mkdir()
    origin = session.stamp()
    request = {'schema_version': 1, 'origin': origin, 'requested_seconds': args.seconds,
               'startup_allowance_seconds': args.startup_allowance,
               'monotonic_scope': 'one orchestrator, one host and boot',
               'synchronization': 'unverified', 'timing_error_ms': None,
               'semantics': 'simultaneous reception; no hardware sample synchronization'}
    session.write_new_json(directory / 'request.json', request)
    common = [sys.executable, str(Path(__file__).resolve()), '_worker']
    tail = ['--episode', str(episode), '--capture-id', 'dual01']
    commands = {
        'armband': common + ['capture'] + tail + ['--port', args.port, '--baud', str(args.baud),
                    '--seconds', str(args.seconds + args.startup_allowance)],
        'ring': common + ['ring-capture'] + tail + ['--address', args.address,
                    '--seconds', str(args.seconds), '--timeout', '10', '--replay-pages', '0']}
    print(f'试次：{episode}\n正在连接并记录；臂环包含 {args.startup_allowance:g} 秒启动预留。', flush=True)
    result = supervise(commands, directory, args.seconds + args.startup_allowance + 100)
    result['ended'] = session.stamp()
    session.write_new_json(directory / 'capture_result.json', result)
    try:
        processing = process_episode(episode, 'dual01', 'dual01', 'dual01', origin['host_monotonic_ns'])
    except (Exception, KeyboardInterrupt) as exc:
        processing = {'errors': [f'postprocessing interrupted/failed: {type(exc).__name__}: {exc}']}
    issues = quality_issues(processing, args.seconds)
    result.update({'episode': str(episode), 'quality_issues': issues,
                   'synchronization': 'unverified', 'timing_error_ms': None,
                   'engineering_capture_ok': result['completed'] and not issues})
    result['exit_code'] = 0 if result['engineering_capture_ok'] else 1
    session.write_new_json(directory / 'result.json', result)
    return result


def reprocess(args):
    episode = session.require_episode(args.episode)
    request = session.read_json(episode / 'dual_run' / 'request.json')
    if request.get('monotonic_scope') != 'one orchestrator, one host and boot':
        raise ValueError('Only original single-host orchestrator runs can be reprocessed here')
    out = episode / 'dual_derived' / session.identifier(args.derive_id)
    if out.exists():
        raise FileExistsError(f'output already exists: {out}')
    try:
        result = process_episode(episode, 'dual01', 'dual01', args.derive_id,
                                 request['origin']['host_monotonic_ns'])
    except (Exception, KeyboardInterrupt) as exc:
        out.mkdir(parents=True, exist_ok=True)
        result = {'errors': [f'{type(exc).__name__}: {exc}'], 'exit_code': 1,
                  'synchronization': 'unverified', 'status': 'processing_failed_or_interrupted'}
        session.write_new_json(out / 'processing_failure.json', result)
        return result
    issues = quality_issues(result, request['requested_seconds'])
    return {'quality_issues': issues, 'synchronization': 'unverified',
            'semantics': 'processing only; original acquisition verdict unchanged',
            'exit_code': 1 if issues else 0}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    r = sub.add_parser('record', help='Create and capture one engineering episode')
    r.add_argument('--root', type=Path, default=session.DEFAULT_ROOT)
    for name in ('subject', 'session', 'trial', 'task', 'consent-scope', 'port', 'address'):
        r.add_argument('--' + name, required=True)
    r.add_argument('--seconds', type=float, default=30)
    r.add_argument('--startup-allowance', type=float, default=30)
    r.add_argument('--baud', type=int, default=115200)
    r.add_argument('--wear-side', choices=['left', 'right', 'bilateral', 'unknown'], default='unknown')
    r.add_argument('--ring-mount', choices=session.RING_MOUNTS, default='unknown')
    r.add_argument('--ring-power', choices=session.RING_POWERS, default='unknown')
    r.add_argument('--notes', default='')
    r.set_defaults(handler=record)
    r = sub.add_parser('inspect')
    r.add_argument('--episode', type=Path, required=True)
    r.add_argument('--output', type=Path, required=True)
    r.set_defaults(handler=lambda a: inspect_episode(a.episode, a.output))
    r = sub.add_parser('process', help='Reprocess an orchestrator episode into a new derive ID')
    r.add_argument('--episode', type=Path, required=True)
    r.add_argument('--derive-id', required=True)
    r.set_defaults(handler=reprocess)
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '_worker':
        def interrupt(*_):
            raise KeyboardInterrupt()
        if os.name == 'nt':
            signal.signal(signal.SIGBREAK, interrupt)
        return session.main(argv[1:])
    try:
        args = parser().parse_args(argv)
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False))
        return result.get('exit_code', 0)
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
