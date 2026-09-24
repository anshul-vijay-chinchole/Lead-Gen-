"""AI copywriter (``writer.type: ai``): an LLM writes a personalised sequence.

Flow per lead::

    build_system_prompt(ctx) + build_user_prompt(lead, ctx)
      -> ctx.llm.complete_json(...)          # OpenAI / Anthropic / compatible
      -> one Message per writer.sequence step (day from the sequence,
         follow-up subjects forced to "", signature + footer appended by code)
      -> guardrails.check_sequence
      -> problems? retry ONCE with the problems listed as feedback
      -> still problems / LLM error -> TemplateWriter fallback (or raise)

Model output is cleaned before the checks: markdown emphasis and code
fences are removed, a sign-off the model added anyway ("Best,\\nSam") is
dropped (the code appends the real signature), literal ``\\n`` sequences are
turned into line breaks. ``personalization`` / ``hypothesis`` come from the
JSON (``personalization_line`` / ``pain_hypothesis``), falling back to the
first line of email 1. ``WriterOutput.writer`` is ``"ai:<model>"``.

Failure handling
----------------
* ``ctx.llm`` is None (no provider / key), or ``ctx.dry_run`` -> template
  output with warning ``ai fallback: <reason>``.
* ``LLMError`` / ``HttpError`` / unparseable JSON -> fallback for that lead.
  A truncated answer (``LLMTruncatedError``) is retried once with twice the
  token budget instead.
* Errors that will not go away - missing credential, HTTP 401/403/404, a
  misconfigured client (``LLMConfigError``), or ``writer.max_llm_failures``
  consecutive failed calls (HTTP errors, refusals, unparseable output) -
  switch the AI off for the rest of the run (logged once) so a bad key or a
  misbehaving model does not cost one failed call per lead. A successful call
  resets the counter.
* ``writer.fallback_to_template: false`` -> raise ``WriterError`` instead.

Playbook keys read
------------------
``writer.fallback_to_template`` (default true), ``writer.max_tokens``
(output budget per call, default 4000 - reasoning models spend part of it
thinking), ``writer.max_llm_failures`` (default 3), plus everything
``prompts`` / ``guardrails`` / ``template`` read.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..context import MissingCredentialError
from ..http import HttpError
from ..llm.base import LLMError
from ..models import Lead, Message
from ..utils import to_int
from .base import Writer, WriterOutput
from .guardrails import check_message, check_sequence
from .prompts import build_feedback_prompt, build_system_prompt, build_user_prompt
from .template import TemplateWriter, append_signoff, build_values, sequence_steps, tidy

DEFAULT_MAX_TOKENS = 4000
MAX_TOKENS_CAP = 16000
DEFAULT_MAX_FAILURES = 3
PERMANENT_HTTP_STATUSES = (401, 403, 404)
MAX_REASON_CHARS = 300  # fallback reasons end up in lead.notes / exports

_SIGNOFF_RE = re.compile(
    r"^(?:best|best regards|kind regards|warm regards|warmly|regards|cheers|thanks|thank you|many thanks|"
    r"all the best|talk soon|speak soon|sincerely|yours sincerely|yours|thanks again)\s*[,.!]?\s*$", re.I)
_PLACEHOLDER_LINE_RE = re.compile(r"^\s*[\[{<]\s*(?:your|sender)[\w\s]*[\]}>]\s*$", re.I)


class WriterError(RuntimeError):
    """The AI writer could not produce acceptable copy and fallback is disabled."""


def clean_subject(value: Any) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    s = re.sub(r"^(?:subject|re)\s*:\s*", "", s, flags=re.I)
    s = s.strip("\"'`*_ ")
    return s


def clean_body(value: Any, sender_name: str = "") -> str:
    """Normalise model output: line breaks, markdown noise, a self-written sign-off."""
    text = str(value or "")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```[a-z]*", "", text)
    text = text.replace("**", "").replace("__", "")
    lines = [ln.rstrip() for ln in text.strip().split("\n")]
    # Drop a trailing sign-off block the model added despite instructions ("Best,\nSam").
    # Only the last 3 non-empty lines are candidates, and everything after the sign-off
    # must look like a name/title (short, not a question) - so body text is never cut.
    names = {n.lower() for n in (sender_name, sender_name.split(" ")[0] if sender_name else "") if n}
    for _ in range(3):  # a long sign-off block may need more than one pass
        nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
        cut = None
        for pos in range(max(1, len(nonempty) - 3), len(nonempty)):
            idx = nonempty[pos]
            ln = lines[idx].strip()
            after = [lines[j].strip() for j in nonempty[pos + 1:]]
            if any(a.endswith("?") or len(a.split()) > 6 for a in after):
                continue
            if _SIGNOFF_RE.match(ln) or _PLACEHOLDER_LINE_RE.match(ln) or ln.lower().strip(",.") in names:
                cut = idx
                break
        if cut is None:
            break
        lines = lines[:cut]
    return tidy("\n".join(lines))


def _first_sentence(body: str) -> str:
    for line in body.split("\n"):
        ln = line.strip()
        if not ln or re.match(r"^(?:hi|hey|hello|dear)\b[^.!?]{0,40},\s*$", ln, re.I):
            continue
        m = re.match(r"(.+?[.!?])(?:\s|$)", ln)
        return (m.group(1) if m else ln).strip()
    return ""


class AIWriter(Writer):
    """LLM-written sequences with guardrails, one feedback retry and template fallback."""

    name = "ai"
    offline = False

    def __init__(self, config: Dict[str, Any], ctx: Any):
        super().__init__(config, ctx)
        self._template: Optional[TemplateWriter] = None
        self._disabled: Optional[str] = None
        self._failures = 0

    # --- settings ----------------------------------------------------------------------------
    @property
    def wcfg(self) -> Dict[str, Any]:
        return self.ctx.playbook.writer

    @property
    def max_tokens(self) -> int:
        n = to_int(self.wcfg.get("max_tokens"))
        return n if n and n > 0 else DEFAULT_MAX_TOKENS

    @property
    def template(self) -> TemplateWriter:
        if self._template is None:
            self._template = TemplateWriter(dict(self.config, type="template"), self.ctx)
        return self._template

    # --- fallback / circuit breaker ------------------------------------------------------------
    def _fallback(self, lead: Lead, reason: str, exc: Optional[BaseException] = None) -> WriterOutput:
        reason = reason if len(reason) <= MAX_REASON_CHARS else reason[: MAX_REASON_CHARS - 1] + "…"
        if self.wcfg.get("fallback_to_template", True) is False:
            raise WriterError(f"ai writer failed for {lead.company.name or lead.company.domain}: {reason}") from exc
        self.log.info("ai writer: template fallback for %s (%s)", lead.company.name, reason)
        out = self.template.write(lead)
        out.warnings.insert(0, f"ai fallback: {reason}")
        return out

    def _disable(self, reason: str) -> None:
        if self._disabled is None:
            self._disabled = reason
            self.log.warning("ai writer disabled for the rest of this run: %s", reason)

    def _on_request_error(self, e: BaseException) -> str:
        """Record an LLM call failure (maybe switching the AI off); returns the reason text."""
        reason = f"{type(e).__name__}: {e}"
        if isinstance(e, MissingCredentialError) or getattr(e, "permanent", False):
            self._disable(str(e))
        elif isinstance(e, HttpError) and e.status in PERMANENT_HTTP_STATUSES:
            self._disable(f"LLM request rejected (HTTP {e.status}): {e.body[:200]}")
        else:  # transient or model-level (refusal, unparseable output): tolerate a few in a row
            self._failures += 1
            limit = to_int(self.wcfg.get("max_llm_failures")) or DEFAULT_MAX_FAILURES
            if self._failures >= limit:
                self._disable(f"{self._failures} consecutive LLM failures (last: {reason[:200]})")
        return reason

    # --- main -------------------------------------------------------------------------------
    def write(self, lead: Lead) -> WriterOutput:
        if self.ctx.dry_run:
            return self._fallback(lead, "dry-run (no LLM calls)")
        if self._disabled:
            return self._fallback(lead, self._disabled)
        llm = self.ctx.llm
        if llm is None:
            return self._fallback(lead, "no LLM available (set writer.provider and its API key)")

        model = str(getattr(llm, "model", "") or getattr(llm, "name", "") or "llm")
        system = build_system_prompt(self.ctx)
        user = build_user_prompt(lead, self.ctx)
        values = build_values(lead, self.ctx)
        prompt, max_tokens = user, self.max_tokens
        problems: List[str] = []

        for attempt in range(2):
            try:
                data = llm.complete_json(system, prompt, max_tokens=max_tokens)
            except (LLMError, HttpError, MissingCredentialError, ValueError, TypeError) as e:
                if attempt == 0 and getattr(e, "truncated", False) and max_tokens < MAX_TOKENS_CAP:
                    max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
                    self.log.info("ai writer: output truncated for %s; retrying with max_tokens=%d",
                                  lead.company.name, max_tokens)
                    continue
                reason = self._on_request_error(e)
                return self._fallback(lead, reason, e)
            self._failures = 0

            messages, personalization, hypothesis, problems = self._build(data, lead, values)
            if not problems:
                return WriterOutput(messages=messages, personalization=personalization,
                                    hypothesis=hypothesis, writer=f"ai:{model}")
            if attempt == 0:
                self.log.info("ai writer: %d guardrail problem(s) for %s; retrying with feedback",
                              len(problems), lead.company.name)
                prompt = build_feedback_prompt(user, problems, data if isinstance(data, dict) else None)

        shown = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
        return self._fallback(lead, f"copy failed guardrails after retry: {shown}")

    def _build(self, data: Any, lead: Lead,
               values: Dict[str, str]) -> Tuple[List[Message], str, str, List[str]]:
        """Map the model's JSON to messages; returns (messages, personalization, hypothesis, problems)."""
        seq = sequence_steps(self.ctx)
        n = len(seq)
        if not isinstance(data, dict):
            return [], "", "", ["response is not a JSON object"]
        emails = data.get("emails")
        if emails is None:
            emails = data.get("sequence") or data.get("messages")
        if not isinstance(emails, list) or not emails:
            return [], "", "", ['response has no "emails" list']

        items = [e for e in emails if isinstance(e, dict)]
        steps = [to_int(e.get("step")) for e in items]
        if sorted(s for s in steps if s is not None) == list(range(1, n + 1)) and len(items) == n:
            by_step = {s: e for s, e in zip(steps, items)}
            ordered: List[Optional[Dict[str, Any]]] = [by_step.get(i) for i in range(1, n + 1)]
        else:
            ordered = [items[i] if i < len(items) else None for i in range(n)]

        problems: List[str] = []
        if len(emails) != n:
            problems.append(f'response has {len(emails)} emails, expected exactly {n}')
        signature, footer = values.get("signature", ""), values.get("footer", "")
        sender = values.get("sender_name", "")
        messages: List[Message] = []
        for i, (step, item) in enumerate(zip(seq, ordered)):
            if item is None:
                problems.append(f"step {i + 1}: missing")
                continue
            body = clean_body(item.get("body") or item.get("text") or "", sender)
            subject = clean_subject(item.get("subject")) if i == 0 else ""
            messages.append(Message(step=i + 1, day=step["day"], subject=subject,
                                    body=append_signoff(body, signature, footer)))
        if problems:  # incomplete: still report per-email problems so one retry can fix everything
            for m in messages:
                problems += check_message(m, lead, self.ctx, m.step - 1, signoff=(signature, footer))
        else:
            problems = check_sequence(messages, lead, self.ctx)

        personalization = str(data.get("personalization_line") or data.get("personalization") or "").strip()
        if not personalization and messages:
            personalization = _first_sentence(clean_body(ordered[0].get("body") if ordered[0] else "", sender))
        hypothesis = str(data.get("pain_hypothesis") or data.get("hypothesis") or "").strip()
        return messages, personalization, hypothesis, problems


__all__ = ["AIWriter", "WriterError", "clean_body", "clean_subject"]
