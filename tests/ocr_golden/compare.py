"""
compare.py — side-by-side OCR accuracy: OpenAI gpt-4o vs Anthropic Claude.

EXPERIMENTAL / LOCAL ONLY. Does NOT touch the production OCR path — `_process_upload`
still uses `main._run_gpt_vision` (OpenAI). This tool runs BOTH models on the same
image(s), using the SAME production VISION_PROMPT, and scores each against a
hand-checked `expected.json` with the shared scorer — so an OpenAI→Anthropic OCR
switch can be decided on numbers, not vibes.

Both calls are logged to ai_call_log under distinct tasks
(`vision_ocr_compare_openai` / `vision_ocr_compare_claude`), so AFTER a run you
can also read the token + estimated-cost comparison from `GET /ai/stats`.

Requirements (all LOCAL — real images live OUTSIDE the repo):
    OPENAI_API_KEY, ANTHROPIC_API_KEY      (the two models)
    DATABASE_URL                           (optional — only for telemetry; best-effort)
    ANTHROPIC_VISION_MODEL                 (optional — set to a Sonnet for a
                                            capability-matched test; default Haiku)

Usage:
    Single: python -m tests.ocr_golden.compare <image> <expected.json>
    Batch:  python -m tests.ocr_golden.compare --dir <folder>
            (every <name>.jpg|.jpeg|.png with a sibling <name>.expected.json)

`expected.json` has the same field shape as tests/ocr_golden/cases/*.json's
`expected` block (vendor_name, invoice_no, bill_date, merchant_tax_id, subtotal,
vat, amount, items[]).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import pathlib
import sys

from tests.ocr_golden.scorer import score_case


def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_strip_fence(raw))


def run_openai_ocr(image_bytes: bytes, mime: str, prompt: str) -> dict:
    """OpenAI path — mirrors main._run_gpt_vision but with a distinct telemetry
    task so it doesn't pollute production vision_ocr stats. Same prompt + model."""
    import os
    from llm import openai_chat

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime or 'image/jpeg'};base64,{b64}"
    resp = openai_chat(
        "vision_ocr_compare_openai",
        model=os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=4000,
    )
    return _parse_json(resp.choices[0].message.content or "{}")


def run_claude_ocr(image_bytes: bytes, mime: str, prompt: str) -> dict:
    """Anthropic path via llm.call_anthropic_vision (logged). Same prompt."""
    from llm import call_anthropic_vision

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    raw = call_anthropic_vision(
        "vision_ocr_compare_claude",
        image_b64=b64,
        mime_type=mime or "image/jpeg",
        prompt=prompt,
        max_tokens=4000,
    )
    return _parse_json(raw)


def run_openai_structured_ocr(image_bytes: bytes, mime: str, prompt: str) -> dict:
    """OpenAI path with a STRICT JSON Schema (Structured Outputs). Same model +
    prompt as the free-form runner, but the output shape is structurally
    guaranteed (see ocr_schema). Routed through llm.openai_chat_structured +
    normalize_structured, logged under a distinct task. Lets `compare` measure
    whether strict mode beats free-form on real invoices."""
    import os
    from llm import openai_chat_structured
    from ocr_schema import invoice_json_schema, normalize_structured

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime or 'image/jpeg'};base64,{b64}"
    resp = openai_chat_structured(
        "vision_ocr_compare_structured",
        schema=invoice_json_schema(),
        schema_name="invoice",
        model=os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=4000,
    )
    return normalize_structured(_parse_json(resp.choices[0].message.content or "{}"))


def compare_results(expected: dict, openai_actual: dict, claude_actual: dict,
                    structured_actual: dict | None = None) -> dict:
    """Pure: score each model output against expected. No network. Testable.
    `structured_actual` (strict-schema OpenAI) is optional so the existing 2-way
    callers keep working; when present the winner is chosen across all three."""
    scores = {
        "openai": score_case(expected, openai_actual),
        "claude": score_case(expected, claude_actual),
    }
    if structured_actual is not None:
        scores["structured"] = score_case(expected, structured_actual)

    best = max(s["overall"] for s in scores.values())
    leaders = [name for name, s in scores.items() if s["overall"] == best]
    winner = leaders[0] if len(leaders) == 1 else "tie"

    out = dict(scores)
    out["winner"] = winner
    return out


def summarize(rows: list[dict]) -> dict:
    """Pure: aggregate per-model mean overall + win counts across compared cases.
    Adapts to whichever models the rows actually contain (2-way or 3-way)."""
    n = len(rows)
    model_keys = ["openai", "claude", "structured"]
    present = [m for m in model_keys if rows and m in rows[0]]
    if not n:
        return {"cases": 0, "openai_mean_overall": 0.0, "claude_mean_overall": 0.0, "wins": {}}

    means = {m: round(sum(r[m]["overall"] for r in rows) / n, 4) for m in present}
    wins: dict[str, int] = {m: 0 for m in present}
    wins["tie"] = 0
    for r in rows:
        wins[r["winner"]] = wins.get(r["winner"], 0) + 1

    best = max(means.values()) if means else 0.0
    leaders = [m for m, v in means.items() if v == best]
    out = {
        "cases": n,
        "wins": wins,
        "recommendation": leaders[0] if len(leaders) == 1 else "tie",
    }
    for m in present:
        out[f"{m}_mean_overall"] = means[m]
    # Back-compat keys (always present even in a 3-way run).
    out.setdefault("openai_mean_overall", means.get("openai", 0.0))
    out.setdefault("claude_mean_overall", means.get("claude", 0.0))
    return out


def _vision_prompt() -> str:
    """The production VISION_PROMPT, so the comparison is apples-to-apples."""
    from main import VISION_PROMPT  # type: ignore
    return VISION_PROMPT.format(ocr_hint="(empty)")


def _compare_one(image_path: pathlib.Path, expected_path: pathlib.Path, prompt: str) -> dict:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    image_bytes = image_path.read_bytes()
    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    openai_actual = run_openai_ocr(image_bytes, mime, prompt)
    claude_actual = run_claude_ocr(image_bytes, mime, prompt)
    structured_actual = run_openai_structured_ocr(image_bytes, mime, prompt)
    res = compare_results(expected, openai_actual, claude_actual, structured_actual)
    res["image"] = image_path.name
    return res


def _print_row(res: dict) -> None:
    o, c = res["openai"], res["claude"]
    s = res.get("structured")
    print(f"\n  {res.get('image', '?')}")
    if s:
        print(f"    {'metric':<18}{'gpt-4o':>12}{'structured':>12}{'claude':>12}")
        print(f"    {'field_accuracy':<18}{o['field_accuracy']:>12}{s['field_accuracy']:>12}{c['field_accuracy']:>12}")
        print(f"    {'item_f1':<18}{o['items']['f1']:>12}{s['items']['f1']:>12}{c['items']['f1']:>12}")
        print(f"    {'overall':<18}{o['overall']:>12}{s['overall']:>12}{c['overall']:>12}")
    else:
        print(f"    {'metric':<18}{'gpt-4o':>12}{'claude':>12}")
        print(f"    {'field_accuracy':<18}{o['field_accuracy']:>12}{c['field_accuracy']:>12}")
        print(f"    {'item_f1':<18}{o['items']['f1']:>12}{c['items']['f1']:>12}")
        print(f"    {'overall':<18}{o['overall']:>12}{c['overall']:>12}")
    print(f"    winner: {res['winner']}")


def main(argv: list[str]) -> int:
    prompt = _vision_prompt()

    if len(argv) == 2 and argv[0] == "--dir":
        folder = pathlib.Path(argv[1])
        rows = []
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            exp = img.with_suffix(img.suffix + ".expected.json")
            if not exp.exists():
                exp = img.with_suffix(".expected.json")
            if not exp.exists():
                print(f"  skip {img.name}: no matching .expected.json")
                continue
            res = _compare_one(img, exp, prompt)
            _print_row(res)
            rows.append(res)
        print("\n=== SUMMARY ===")
        print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
        print("\nToken + estimated-cost per model: see GET /ai/stats "
              "(tasks vision_ocr_compare_openai / _structured / _claude)")
        return 0

    if len(argv) == 2:
        res = _compare_one(pathlib.Path(argv[0]), pathlib.Path(argv[1]), prompt)
        _print_row(res)
        print("\nToken + estimated-cost per model: see GET /ai/stats "
              "(tasks vision_ocr_compare_openai / _structured / _claude)")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
