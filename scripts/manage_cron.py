#!/usr/bin/env python3
"""Install or remove the managed user-crontab fallback."""

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


BEGIN = "# BEGIN OPPORTUNITY RADAR MANAGED SCHEDULE"
END = "# END OPPORTUNITY RADAR MANAGED SCHEDULE"
DEFAULT_LABEL = "io.github.opportunity-radar.monitor"
SAFE_LABEL = re.compile(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\Z")
CRONTAB = "/usr/bin/crontab"


def calendar_value(value: str, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("{} must be an integer".format(label)) from error
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError("{} must be between {} and {}".format(label, minimum, maximum))
    return parsed


def current_crontab() -> str:
    result = subprocess.run(
        [CRONTAB, "-l"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode == 0:
        return result.stdout
    if "no crontab" in result.stderr.lower():
        return ""
    raise RuntimeError("Unable to read the user crontab")


def cron_label(value: str) -> str:
    if not value or len(value) > 128 or not SAFE_LABEL.fullmatch(value):
        raise argparse.ArgumentTypeError("label contains unsafe characters")
    return value


def cron_markers(label: str = DEFAULT_LABEL) -> Tuple[str, str]:
    validated = cron_label(label)
    if validated == DEFAULT_LABEL:
        return BEGIN, END
    return "{} [{}]".format(BEGIN, validated), "{} [{}]".format(END, validated)


def validate_runtime(runtime: Path) -> Path:
    value = str(runtime)
    if not runtime.is_absolute():
        raise ValueError("cron runtime must be an absolute path")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("cron runtime contains control characters")
    return runtime


def without_managed_block(content: str, label: str = DEFAULT_LABEL) -> str:
    begin_marker, end_marker = cron_markers(label)
    output = []
    managed = False
    begin_count = 0
    end_count = 0
    for line in content.splitlines():
        if line == begin_marker:
            if managed or begin_count:
                raise ValueError("Malformed Opportunity Radar crontab markers")
            managed = True
            begin_count += 1
            continue
        if line == end_marker:
            if not managed or end_count:
                raise ValueError("Malformed Opportunity Radar crontab markers")
            managed = False
            end_count += 1
            continue
        if not managed:
            output.append(line)
    if managed or begin_count != end_count:
        raise ValueError("Malformed Opportunity Radar crontab markers")
    while output and not output[-1].strip():
        output.pop()
    return "\n".join(output)


def cron_quote(path: Path) -> str:
    return shlex.quote(str(path)).replace("%", r"\%")


def build_crontab(
    content: str,
    runtime: Path,
    times: List[Tuple[int, int]],
    label: str = DEFAULT_LABEL,
) -> str:
    runtime = validate_runtime(runtime)
    begin_marker, end_marker = cron_markers(label)
    base = without_managed_block(content, label)
    command = cron_quote(runtime / "scripts" / "run_monitor.sh")
    stdout = cron_quote(runtime / "logs" / "cron.out.log")
    stderr = cron_quote(runtime / "logs" / "cron.err.log")
    lines = [base, begin_marker] if base else [begin_marker]
    for hour, minute in times:
        lines.append("{} {} * * * {} >> {} 2>> {}".format(minute, hour, command, stdout, stderr))
    lines.append(end_marker)
    return "\n".join(lines) + "\n"


def write_crontab(content: str) -> None:
    subprocess.run([CRONTAB, "-"], input=content, text=True, check=True)


def has_managed_block(content: str, label: str = DEFAULT_LABEL) -> bool:
    begin_marker, _end_marker = cron_markers(label)
    without_managed_block(content, label)
    return any(line == begin_marker for line in content.splitlines())


def managed_block(content: str, label: str = DEFAULT_LABEL) -> List[str]:
    begin_marker, end_marker = cron_markers(label)
    without_managed_block(content, label)
    lines = content.splitlines()
    if begin_marker not in lines:
        return []
    start = lines.index(begin_marker)
    end = lines.index(end_marker, start + 1)
    return lines[start : end + 1]


def restore_managed_state(
    current: str,
    prior: str,
    label: str = DEFAULT_LABEL,
) -> str:
    current_block = managed_block(current, label)
    prior_block = managed_block(prior, label)
    if not current_block and not prior_block:
        return current
    base = without_managed_block(current, label)
    lines = [base] if base else []
    lines.extend(prior_block)
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--runtime", type=Path, required=True)
    install.add_argument("--label", type=cron_label, default=DEFAULT_LABEL)
    install.add_argument("--morning-hour", default="7")
    install.add_argument("--morning-minute", default="30")
    install.add_argument("--afternoon-hour", default="16")
    install.add_argument("--afternoon-minute", default="30")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--runtime", type=Path, required=True)
    verify.add_argument("--label", type=cron_label, default=DEFAULT_LABEL)
    verify.add_argument("--morning-hour", default="7")
    verify.add_argument("--morning-minute", default="30")
    verify.add_argument("--afternoon-hour", default="16")
    verify.add_argument("--afternoon-minute", default="30")
    for command in ("remove", "status", "snapshot", "restore"):
        action = subparsers.add_parser(command)
        action.add_argument("--label", type=cron_label, default=DEFAULT_LABEL)
    args = parser.parse_args()

    content = current_crontab()
    if args.command == "snapshot":
        without_managed_block(content, args.label)
        sys.stdout.write(content)
        return 0
    if args.command == "restore":
        prior = sys.stdin.read()
        restored = restore_managed_state(content, prior, args.label)
        if content != restored:
            write_crontab(restored)
        return 0
    if args.command == "status":
        installed = has_managed_block(content, args.label)
        print("installed" if installed else "not installed")
        return 0 if installed else 1
    if args.command == "remove":
        begin_marker, _end_marker = cron_markers(args.label)
        cleaned = without_managed_block(content, args.label)
        if not any(line == begin_marker for line in content.splitlines()):
            return 0
        write_crontab(cleaned + ("\n" if cleaned else ""))
        return 0

    times = [
        (
            calendar_value(args.morning_hour, 0, 23, "morning hour"),
            calendar_value(args.morning_minute, 0, 59, "morning minute"),
        ),
        (
            calendar_value(args.afternoon_hour, 0, 23, "afternoon hour"),
            calendar_value(args.afternoon_minute, 0, 59, "afternoon minute"),
        ),
    ]
    if args.command == "verify":
        expected = managed_block(
            build_crontab("", args.runtime, times, args.label),
            args.label,
        )
        return 0 if managed_block(content, args.label) == expected else 1
    write_crontab(build_crontab(content, args.runtime, times, args.label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
