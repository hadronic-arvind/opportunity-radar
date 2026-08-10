#!/usr/bin/env python3
"""Install or remove the managed user-crontab fallback."""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


BEGIN = "# BEGIN OPPORTUNITY RADAR MANAGED SCHEDULE"
END = "# END OPPORTUNITY RADAR MANAGED SCHEDULE"
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


def without_managed_block(content: str) -> str:
    output = []
    managed = False
    begin_count = 0
    end_count = 0
    for line in content.splitlines():
        if line == BEGIN:
            if managed or begin_count:
                raise ValueError("Malformed Opportunity Radar crontab markers")
            managed = True
            begin_count += 1
            continue
        if line == END:
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


def build_crontab(content: str, runtime: Path, times: List[Tuple[int, int]]) -> str:
    base = without_managed_block(content)
    command = cron_quote(runtime / "scripts" / "run_monitor.sh")
    stdout = cron_quote(runtime / "logs" / "cron.out.log")
    stderr = cron_quote(runtime / "logs" / "cron.err.log")
    lines = [base, BEGIN] if base else [BEGIN]
    for hour, minute in times:
        lines.append("{} {} * * * {} >> {} 2>> {}".format(minute, hour, command, stdout, stderr))
    lines.append(END)
    return "\n".join(lines) + "\n"


def write_crontab(content: str) -> None:
    subprocess.run([CRONTAB, "-"], input=content, text=True, check=True)


def has_managed_block(content: str) -> bool:
    without_managed_block(content)
    return any(line == BEGIN for line in content.splitlines())


def managed_block(content: str) -> List[str]:
    without_managed_block(content)
    lines = content.splitlines()
    if BEGIN not in lines:
        return []
    start = lines.index(BEGIN)
    end = lines.index(END, start + 1)
    return lines[start : end + 1]


def restore_managed_state(current: str, prior: str) -> str:
    current_block = managed_block(current)
    prior_block = managed_block(prior)
    if not current_block and not prior_block:
        return current
    base = without_managed_block(current)
    lines = [base] if base else []
    lines.extend(prior_block)
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install")
    install.add_argument("--runtime", type=Path, required=True)
    install.add_argument("--morning-hour", default="7")
    install.add_argument("--morning-minute", default="30")
    install.add_argument("--afternoon-hour", default="16")
    install.add_argument("--afternoon-minute", default="30")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--runtime", type=Path, required=True)
    verify.add_argument("--morning-hour", default="7")
    verify.add_argument("--morning-minute", default="30")
    verify.add_argument("--afternoon-hour", default="16")
    verify.add_argument("--afternoon-minute", default="30")
    subparsers.add_parser("remove")
    subparsers.add_parser("status")
    subparsers.add_parser("snapshot")
    subparsers.add_parser("restore")
    args = parser.parse_args()

    content = current_crontab()
    if args.command == "snapshot":
        without_managed_block(content)
        sys.stdout.write(content)
        return 0
    if args.command == "restore":
        prior = sys.stdin.read()
        restored = restore_managed_state(content, prior)
        if content != restored:
            write_crontab(restored)
        return 0
    if args.command == "status":
        installed = has_managed_block(content)
        print("installed" if installed else "not installed")
        return 0 if installed else 1
    if args.command == "remove":
        cleaned = without_managed_block(content)
        if not any(line == BEGIN for line in content.splitlines()):
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
        expected = managed_block(build_crontab("", args.runtime, times))
        return 0 if managed_block(content) == expected else 1
    write_crontab(build_crontab(content, args.runtime, times))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
