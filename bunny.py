#!/usr/bin/env python3
"""Draw a parade of bunnies on a GitHub contribution graph. Standard library only.

    python3 bunny.py preview            print the last year of bunnies, commits nothing
    python3 bunny.py preview --svg F    also write the picture to F
    python3 bunny.py run --repo DIR     commit what is missing and push it
        --dry-run                       only say what would be committed
        --include-today                 do not wait for the evening to draw today
"""
import argparse
import datetime as dt
import html
import re
import subprocess
import sys
import urllib.request

USER = "putrinaq"
NAME = "putrinaq"
EMAIL = "147160795+putrinaq@users.noreply.github.com"
TZ = dt.timezone(dt.timedelta(hours=8))  # Malaysia
BRANCH = "main"
TAG = "bunny:"          # subject prefix that marks the commits this script made
READY_HOUR = 21         # today is drawn after this hour, once the real work is in

# Commits per day for each shade. GitHub splits the shades at 1/4, 2/4 and 3/4 of
# the busiest ordinary day, so with 40 the splits are at 10, 20 and 30.
COUNTS = {1: 1, 2: 15, 3: 25, 4: 40}

# 7 rows, Sunday on top. Digits are shades; "." is grass (shade 1).
SPRITES = {
    "face": [
        ".4...4.",
        ".4...4.",
        ".44.44.",
        "4444444",
        "44.4.44",
        "4442444",
        ".44444.",
    ],
    "sit": [
        "....4.4..",
        "....4.4..",
        "....4444.",
        "..4444.44",
        ".44444444",
        "2444444..",
        ".444.44..",
    ],
    "tail": [
        ".4...4.",
        ".4...4.",
        ".44.44.",
        ".44444.",
        "4442444",
        "4422244",
        ".44444.",
    ],
    "heart": [
        ".....",
        ".....",
        ".3.3.",
        "33333",
        ".333.",
        "..3..",
        ".....",
    ],
    "carrot": [
        "...",
        "2.2",
        ".2.",
        "333",
        "333",
        ".3.",
        ".3.",
    ],
}
PARADE = ["carrot", "face", "heart", "sit", "heart", "tail"]
PHASE = 22              # shifts the whole parade, in weeks
EPOCH = dt.date(2023, 1, 1)  # a Sunday


def strip(parade):
    """Join the sprites into 7 long rows with one column of grass between them."""
    return ["".join(SPRITES[s][r] + "." for s in parade) for r in range(7)]


def shade(day, rows=None, phase=None):
    rows = rows or strip(PARADE)
    phase = PHASE if phase is None else phase
    weeks, row = divmod((day - EPOCH).days, 7)
    ch = rows[row][(weeks + phase) % len(rows[0])]
    return 1 if ch == "." else int(ch)


def graph():
    """{date: contributions} as shown on the public profile."""
    req = urllib.request.Request(
        f"https://github.com/users/{USER}/contributions",
        headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return parse_graph(r.read().decode())


def parse_graph(page):
    ids = {}
    for td in re.findall(r"<td[^>]*ContributionCalendar-day[^>]*>", page):
        day = re.search(r'data-date="([\d-]+)"', td)
        ident = re.search(r'\bid="([^"]+)"', td)
        if day and ident:
            ids[ident.group(1)] = dt.date.fromisoformat(day.group(1))
    out = {}
    for ident, text in re.findall(r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>([^<]*)', page):
        m = re.match(r"\s*(No|[\d,]+) contribution", html.unescape(text))
        if ident in ids and m:
            out[ids[ident]] = 0 if m.group(1) == "No" else int(m.group(1).replace(",", ""))
    if len(out) < 300:
        raise SystemExit(f"could not read the contribution graph ({len(out)} days found)")
    return out


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, text=True,
                          encoding="utf-8", stdout=subprocess.PIPE).stdout


def own_commits(repo, base):
    days = {}
    log = git(repo, "log", "--format=%ad\t%s", "--date=format:%Y-%m-%d", base)
    for line in log.splitlines():
        day, subject = line.split("\t", 1)
        if subject.startswith(TAG):
            day = dt.date.fromisoformat(day)
            days[day] = days.get(day, 0) + 1
    return days


def plan(seen, own, now, include_today):
    """[(date, commits to add)]; a day that already has bunny commits is finished."""
    today = now.date()
    last = today if include_today or now.hour >= READY_HOUR else today - dt.timedelta(1)
    todo = []
    day = min(seen)
    while day <= last:
        if not own.get(day):
            need = COUNTS[shade(day)] - seen.get(day, 0)
            if need > 0:
                todo.append((day, need))
        day += dt.timedelta(1)
    return todo


def fast_import(repo, base, todo):
    """Add every commit in one go: either all of them land or none do."""
    parts = []
    for day, count in todo:
        for i in range(count):
            # 09:00 to 20:00, evenly spread
            when = dt.datetime.combine(day, dt.time(9), TZ) + dt.timedelta(minutes=660 * i // count)
            stamp = f"{int(when.timestamp())} {when:%z}"
            msg = f"{TAG} {day} hop {i + 1}/{count}\n".encode()
            body = f"{day} hop {i + 1} of {count}\n".encode()
            head = (f"commit refs/heads/{BRANCH}\n"
                    f"author {NAME} <{EMAIL}> {stamp}\ncommitter {NAME} <{EMAIL}> {stamp}\n"
                    f"data {len(msg)}\n").encode() + msg
            if not parts:
                head += f"from {base}\n".encode()
            parts.append(head + f"M 100644 inline burrow.txt\ndata {len(body)}\n".encode() + body + b"\n")
    parts.append(b"done\n")
    subprocess.run(["git", "-C", repo, "fast-import", "--quiet", "--done", "--force"],
                   input=b"".join(parts), check=True)


def run(args):
    repo, now = args.repo, dt.datetime.now(TZ)
    git(repo, "fetch", "--quiet", "origin")
    remote = git(repo, "for-each-ref", "--format=%(objectname)", f"refs/remotes/origin/{BRANCH}").strip()
    # An empty GitHub repo has no branch yet: start from the local code instead.
    base = remote or git(repo, "rev-parse", "HEAD").strip()
    todo = plan(graph(), own_commits(repo, base), now, args.include_today)
    total = sum(n for _, n in todo)
    print(f"{len(todo)} days to draw, {total} commits"
          + (f", {todo[0][0]} to {todo[-1][0]}" if todo else ""))
    if args.dry_run or (not todo and remote):
        return
    if remote:
        git(repo, "reset", "--quiet", "--hard", remote)
    if todo:
        fast_import(repo, base, todo)
        git(repo, "reset", "--quiet", "--hard", BRANCH)
    git(repo, "push", "--quiet", "origin", f"{BRANCH}:{BRANCH}")
    print("pushed")


def preview(args):
    today = dt.datetime.now(TZ).date()
    first = today - dt.timedelta((today - EPOCH).days % 7 + 52 * 7)
    cells = [[shade(first + dt.timedelta(c * 7 + r)) for c in range(53)] for r in range(7)]
    print("\n".join("".join("  ░░▒▒▓▓██"[(v * 2):(v * 2 + 2)] for v in row) for row in cells))
    if args.svg:
        with open(args.svg, "w", encoding="utf-8", newline="\n") as f:
            f.write(svg(cells))


def svg(cells):
    colors = {0: "#ebedf0", 1: "#9be9a8", 2: "#40c463", 3: "#30a14e", 4: "#216e39"}
    rects = "".join(
        f'<rect x="{10 + c * 13}" y="{10 + r * 13}" width="10" height="10" rx="2" fill="{colors[v]}"/>\n'
        for r, row in enumerate(cells) for c, v in enumerate(row))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {len(cells[0]) * 13 + 17} 108">\n'
            f'<title>bunny parade</title>\n<rect width="100%" height="100%" rx="6" fill="#fff"/>\n{rects}</svg>\n')


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("preview")
    a.add_argument("--svg")
    a.set_defaults(fn=preview)
    a = sub.add_parser("run")
    a.add_argument("--repo", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--include-today", action="store_true")
    a.set_defaults(fn=run)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
