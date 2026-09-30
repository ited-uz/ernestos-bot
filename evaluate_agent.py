"""Opt-in FREE-plan parser / recorded-audio evaluation. Never writes app data.

Usage: python evaluate_agent.py --run-free-api
       python evaluate_agent.py --run-free-api --audio voice.ogg --reference "..."
       AGENT_PROVIDER=gemini python evaluate_agent.py --run-free-api --audio voice.ogg
Uses AGENT_PROVIDER (groq or gemini) and its own key (GROQ_API_KEY /
GEMINI_API_KEY) on a free plan. Each case uses provider quota. Run the same
recordings through both providers to decide which one stays primary.
This is not run by pytest and does not provision accounts or change billing.
"""
import argparse
import asyncio
import json
import mimetypes
from pathlib import Path

import config
import agent_provider as provider

CONTEXT = {"today": "2026-09-30", "timezone": "Asia/Tashkent", "language": "uz", "truncated": False,
           "teams": [], "items": [
               {"entity": "task", "scope": "personal", "id": 101, "name": "Hisobot", "team_id": None, "status": "waiting", "deadline": "2026-10-01"},
               {"entity": "habit", "scope": "personal", "id": 201, "name": "Kitob", "team_id": None, "system_key": None},
               {"entity": "project", "scope": "personal", "id": 301, "name": "Sayt", "team_id": None},
           ]}


def check(case, output):
    errors = []
    # Named proper nouns alone do not necessarily constitute code-switching.
    allowed_languages = {case["language"]}
    if case["language"] == "mixed":
        allowed_languages |= {"uz", "ru", "en"}
    if output.language not in allowed_languages:
        errors.append("language")
    if case.get("clarify"):
        if output.actions or not output.question:
            errors.append("must_clarify")
    elif output.question or len(output.actions) != 1:
        errors.append("action_count_or_question")
    else:
        a = output.actions[0]
        for field in ("entity", "operation", "target_id"):
            if field in case and getattr(a, field) != case[field]:
                errors.append(field)
        fields = {c.field: c.value for c in a.changes}
        for field, value in case.get("fields", {}).items():
            if fields.get(field) != value:
                errors.append(field)
    return errors


def word_error_rate(reference, hypothesis):
    """Basic normalized WER; not the same as correct-command percentage."""
    import re
    def words(text):
        text = text.casefold().replace("‘", "'").replace("’", "'").replace("ʻ", "'")
        return re.findall(r"[\w']+", text)
    ref, hyp = words(reference), words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        row = [i]
        for j, h in enumerate(hyp, 1):
            row.append(min(row[j-1] + 1, previous[j] + 1, previous[j-1] + (r != h)))
        previous = row
    return previous[-1] / max(1, len(ref))


async def run(args):
    if not args.run_free_api:
        raise SystemExit("No network calls made. Pass --run-free-api after configuring a FREE-plan key.")
    config.AGENT_ENABLED, config.AGENT_FALLBACK_PROVIDER = True, ""
    if not provider.configured():
        raise SystemExit("Set AGENT_PROVIDER and its API key in your local environment, never in this file.")
    if args.audio:
        path = Path(args.audio)
        if path.stat().st_size > config.AGENT_AUDIO_BYTES:
            raise SystemExit("Audio exceeds 10 MB.")
        transcript = await provider.transcribe(path.read_bytes(), mimetypes.guess_type(path)[0] or "audio/ogg", CONTEXT)
        report = {"provider": config.AGENT_PROVIDER, "transcript": transcript}
        if args.reference:
            report["word_error_rate"] = word_error_rate(args.reference, transcript)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    cases = json.loads((Path(__file__).parent / "tests/agent_language_cases.json").read_text())
    results = []
    for case in cases:
        try:
            output = await provider.plan(case["text"], CONTEXT, [])
            errors = check(case, output)
            results.append({"id": case["id"], "pass": not errors, "errors": errors, "output": output.model_dump()})
        except Exception as exc:
            results.append({"id": case["id"], "pass": False, "error": type(exc).__name__})
        # Single-threaded and paced for shared free-tier TPM. Ctrl-C is safe.
        if case is not cases[-1]:
            await asyncio.sleep(35)
    print(json.dumps({"provider": config.AGENT_PROVIDER, "model": provider.text_model(config.AGENT_PROVIDER), "passed": sum(r["pass"] for r in results),
                      "total": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-free-api", action="store_true")
    parser.add_argument("--audio")
    parser.add_argument("--reference")
    asyncio.run(run(parser.parse_args()))
