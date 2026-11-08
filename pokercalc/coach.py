"""Coach analysis: sends your stats and recent hands to Claude (via the local `claude` CLI)."""
import json
import os
import shutil
import subprocess
import tempfile

from . import analytics

SYSTEM = """You are a candid, practical poker coach reviewing a student's results from a personal \
study tool. The student plays 6-max cash games on PLAY-MONEY tables only and uses the tool \
to learn. Use ONLY the data given; never invent numbers or hands.

Facts about the data: 'equity' and 'required' come from the tool's Monte Carlo estimate against an \
assumed opponent range (a guess, not what the opponent really held). 'suggested' is the tool's simple \
rule-of-thumb advice, not a solver. Top-ups (deposits) are excluded from results. Play-money fields are \
usually loose and passive, so exploiting them beats GTO-style play.

Write in plain, direct English with these markdown sections, using short bullets:
## Overall picture
## What stands out
## Biggest leaks
## How to improve
## What to track next

Rules: be honest about sample size (say when numbers are too small to trust and what to look at \
instead); point to specific recent hands when useful (describe them like "A-K on K-7-2 flop"); give \
concrete drills or rules the student can apply at the table next session (not generic poker platitudes); \
keep it under about 600 words; no preamble and no closing offer."""


def build_prompt(con, recent=30):
    s = analytics.summary(con)
    last = lambda a: next((v for v in reversed(a) if v is not None), None)
    first = lambda a: next((v for v in a if v is not None), None)
    r = lambda v, n=1: None if v is None else round(v, n)
    stats = {
        "sample": {"hands_saved": s["hands"], "hands_with_result": len(s["cum_results"]),
                   "finished_sessions": len(s["session_pl"])},
        "results": {"net_result_chips": s["total_pl"], "top_ups_excluded": s["total_deposits"],
                    "bb_per_100": r(s["bb_per_100"]), "hands_won_share": r(s["win_rate"], 3),
                    "recent_20_hand_win_rate_pct": r(last(s["win_rate_roll"])),
                    "per_session": [{"result": p["pl"], "hands": p["hands"], "bb_per_100": r(p["bb100"])}
                                    for p in s["per_session"]]},
        "calls": {"count": len(s["call_margins"]), "below_break_even": s["bad_calls"],
                  "good_call_rate": r(s["good_call_rate"], 3),
                  "expected_value_of_all_calls_chips": r(s["ev_calls"], 0),
                  "expected_value_lost_on_bad_calls_chips": r(s["ev_bad_calls"], 0),
                  "avg_equity_when_calling_pct_start_vs_now": [r(first(s["call_equity_roll"])), r(last(s["call_equity_roll"]))],
                  "avg_equity_needed_pct_start_vs_now": [r(first(s["call_required_roll"])), r(last(s["call_required_roll"]))]},
        "style": {"preflop_vpip": r(s["vpip"], 3), "followed_tool_suggestion_rate": r(s["follow_rate"], 3),
                  "actions_taken": s["action_counts"],
                  "result_when_following_vs_not": s["advice_split"]},
        "by_position_net_chips": s["by_position"],
        "by_street_net_chips": s["by_street"],
    }
    rows = con.execute("SELECT hole, board, pot, call, opps, range_pct, position, stack, equity, required, "
                       "suggested, taken, result FROM hands ORDER BY ts DESC LIMIT ?", (recent,)).fetchall()
    hands = [{k: (round(row[k], 3) if isinstance(row[k], float) else row[k]) for k in row.keys()}
             for row in reversed(rows)]
    return ("Here are my stats and my most recent hands (oldest first). Coach me.\n\n"
            "STATS:\n" + json.dumps(stats, indent=1) +
            f"\n\nRECENT HANDS (last {len(hands)}):\n" + json.dumps(hands))


def find_claude():
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def run_coach(prompt, runner=subprocess.run, timeout=240):
    """Returns (ok, text). The CLI runs in an empty temp dir with tools disabled."""
    exe = find_claude()
    if not os.path.exists(exe):
        return False, "The `claude` command-line tool was not found on this machine."
    with tempfile.TemporaryDirectory() as cwd:
        try:
            p = runner([exe, "-p", "--tools", "", "--no-session-persistence", "--disable-slash-commands",
                        "--output-format", "text", "--system-prompt", SYSTEM],
                       input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "The coach took too long to answer. Try again."
        except OSError as e:
            return False, f"Could not run the claude tool: {e}"
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        return False, (p.stderr or out or "The coach returned nothing.").strip()[:400]
    return True, out
