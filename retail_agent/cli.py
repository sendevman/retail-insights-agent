import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from .agent import Analyst
from .demo import DemoModel, DemoRunner
from .observability import Events
from .safety import SafeError, redact
from .storage import Store

HELP = """Ask a retail analysis question, follow up, or request a report with action items.
Commands:
  /help                         Show this help
  /reports                      List your saved reports
  /show REPORT_ID                Read one of your saved reports
  /save                         Save the last completed analysis
  /prefer tables|bullets         Persist your presentation preference
  /delete conversation          Preview reports from this conversation
  /delete mentioning Client X    Preview your reports containing a literal phrase
  /confirm TOKEN                Confirm the exact preview within 120 seconds
  /cancel                       Cancel pending deletion
  /quit                         Exit
Natural deletion: Delete all reports mentioning Client X
                  Delete all the reports we made in this conversation
Only the exact /confirm TOKEN command executes deletion; 'yes' is insufficient.
"""


class Chat:
    def __init__(self, analyst, store, owner, session, events):
        self.analyst, self.store, self.owner = analyst, store, owner
        self.session, self.events = session, events
        self.last = None

    def handle(self, text):
        text = text.strip()
        if len(text) > 4000:
            return "Please shorten the input to 4,000 characters."
        trace = secrets.token_hex(12)
        try:
            if text == "/help":
                return HELP
            if text == "/reports":
                rows = self.store.list(self.owner)
                return "\n".join(f"{r['id']}  {r['title']}" for r in rows) or "No saved reports."
            if text.startswith("/show "):
                return self.store.read(self.owner, text[6:].strip())
            if text.startswith("/prefer "):
                self.store.set_preference(self.owner, text[8:].strip())
                return "Preference saved."
            if text == "/cancel":
                self.store.cancel(self.owner, self.session)
                self.events.emit(trace, "delete_cancelled")
                return "Pending deletion cancelled."
            if text.startswith("/confirm "):
                count = self.store.confirm_delete(self.owner, self.session, text[9:].strip())
                self.events.emit(trace, "delete_executed", count=count)
                return f"Deleted {count} report(s) from your library. Trace: {trace}"
            scope, term = None, ""
            if text == "/delete conversation" or re.fullmatch(
                r"delete (?:all )?(?:the )?reports (?:we (?:made|created) in |(?:from|in) )"
                r"this conversation[.!]?",
                text,
                re.I,
            ):
                scope = "conversation"
            elif text.startswith("/delete mentioning "):
                scope, term = "mention", text[len("/delete mentioning ") :].strip()
            else:
                match = re.fullmatch(
                    r"delete (?:all )?(?:the )?reports mentioning (.+)", text, re.I
                )
                if match:
                    scope, term = "mention", match.group(1).strip().strip('"')
            if scope:
                token, rows = self.store.prepare_delete(self.owner, self.session, scope, term)
                self.events.emit(trace, "delete_previewed", count=len(rows))
                if not rows:
                    return "No matching reports in your library."
                return (
                    "Delete these reports?\n"
                    + "\n".join(f"- {r['id']}: {r['title']}" for r in rows)
                    + f"\nThis preview expires in 120 seconds.\nType exactly: /confirm {token}"
                    "\nUse /cancel to keep them. Other chat messages cancel this preview."
                )
            if text == "/save":
                return self._save()
            if text.startswith("/"):
                return "Unknown command. Use /help."
            if re.match(r"^(delete|remove|erase|purge)\b", text, re.I):
                return "Choose /delete conversation or /delete mentioning <literal phrase>."
            # Any normal chat turn invalidates outstanding destructive approvals.
            self.store.cancel(self.owner, self.session)
            result = self.analyst.ask(text, self.store.preference(self.owner))
            self.last = result if result["report_ready"] else None
            output = result["text"]
            if result["report_ready"] and re.search(
                r"\b(create|generate|make|save)\b.*\breport\b", text, re.I
            ):
                output += "\n\n" + self._save()
            return output
        except Exception as exc:
            self.events.emit(trace, "command_failed", code=type(exc).__name__)
            message = (
                str(exc) if isinstance(exc, SafeError) else "Command failed. Please try again."
            )
            return redact(message) + f"\nTrace: {trace}"

    def _save(self):
        if not self.last:
            return "Complete an analysis before saving a report."
        report_id = self.store.save(self.owner, self.session, self.last["title"], self.last["text"])
        self.events.emit(self.last["trace_id"], "report_saved")
        return f"Report saved: {report_id}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scoped retail data analysis assistant")
    parser.add_argument("--demo", action="store_true", help="Synthetic offline data/model")
    parser.add_argument("--user", default="alice", help="Trusted local test identity")
    parser.add_argument("--session", default=None, help="Resume report conversation scope")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    load_dotenv(root / ".env")
    # Third-party debug logs can include payloads; use our allowlisted events instead.
    logging.disable(logging.CRITICAL)
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    runtime = (args.runtime or root / "runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        config = json.loads((root / "config/access.json").read_text())
        allowed = config.get(args.user, {}).get("product_ids", [])
        if not allowed or any(type(p) is not int or p <= 0 for p in allowed):
            raise SafeError(
                "Unknown user or no assigned products. Ask the operator to configure access."
            )
        events = Events(runtime / "events.jsonl")
        store = Store(runtime / "reports.sqlite3")
        if args.demo:
            model, runner = DemoModel(), DemoRunner()
            secret = "synthetic-demo-secret-not-for-production"
        else:
            from .bq_client import BigQueryRunner
            from .llm import Gemini

            key = os.getenv("GEMINI_API_KEY")
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            secret = os.getenv("PSEUDONYM_SECRET", "")
            if not key or not project or len(secret) < 32:
                raise SafeError(
                    "Set GEMINI_API_KEY, GOOGLE_CLOUD_PROJECT and a stable "
                    "PSEUDONYM_SECRET of at least 32 characters in .env."
                )
            model = Gemini(key, os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), events)
            runner = BigQueryRunner(
                project,
                os.getenv("BIGQUERY_LOCATION", "US"),
                int(os.getenv("MAX_BYTES_BILLED", "1000000000")),
            )
        session = args.session or secrets.token_hex(8)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session):
            raise SafeError("Session must be 1–80 letters, digits, underscores or hyphens.")
        # Reduce cross-user linking of opaque customer keys.
        user_secret = hmac.new(secret.encode(), args.user.encode(), hashlib.sha256).hexdigest()
        analyst = Analyst(model, runner, events, allowed, user_secret, root)
        chat = Chat(analyst, store, args.user, session, events)
        mode = (
            "DEMO — synthetic data and deterministic planner"
            if args.demo
            else "LIVE — Gemini + BigQuery"
        )
        print(
            f"Retail Analyst | {mode}\nUser: {args.user} | Conversation: {session}\n"
            f"Authorized products: {', '.join(map(str, allowed))}\n/help for commands."
        )
        while True:
            try:
                text = input("\nyou> ")
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break
            if text.strip() in {"/quit", "/exit"}:
                break
            if text.strip():
                print("\n" + redact(chat.handle(text)))
        store.cancel(args.user, session)
        store.db.close()
    except (SafeError, ValueError, OSError, sqlite3.Error) as exc:
        print(
            str(exc)
            if isinstance(exc, SafeError)
            else "Startup failed. Check configuration and local file permissions."
        )
        return 2
    except Exception:
        print("Startup failed. Check BigQuery credentials, network and configuration.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
