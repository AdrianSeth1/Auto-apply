"""Per-field fill-details persistence across the ATS adapter layer.

Regression coverage for the bug that surfaced when the Review queue's
"N of M fields filled" badge opened to "No per-field details were
recorded" -- ``GenericAdapter.apply()`` overrides the base method and
was forgetting to copy ``self._last_fill_details`` onto the result,
so every ``company_site`` / ``workday`` apply produced an empty log.

The tests are deliberately schema-level (no real browser):

* ``_record_fill_details`` must serialise ``FieldMapping`` objects into
  the JSONB-friendly dict shape we persist on ``applications``.
* The result payload serializer must round-trip the new field so the
  Web UI receives it (older adapters that have not migrated yet fall
  back to an empty list rather than ``KeyError``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.execution.ats.base import ApplicationResult, BaseATSAdapter
from src.execution.form_filler import FieldMapping, FormField


class _DummyAdapter(BaseATSAdapter):
    """Bare subclass so we can exercise the base helpers without
    pulling in a full ATS implementation."""

    ats_name = "dummy"

    async def fill_form(self, page, profile_data, qa_responses=None):  # pragma: no cover
        raise NotImplementedError

    async def upload_files(self, page, resume_path=None, cover_letter_path=None):  # pragma: no cover
        raise NotImplementedError

    async def answer_questions(self, page, qa_responses=None):  # pragma: no cover
        raise NotImplementedError

    async def submit(self, page):  # pragma: no cover
        raise NotImplementedError


def _make_adapter() -> _DummyAdapter:
    return _DummyAdapter(browser=MagicMock())


def test_record_fill_details_serialises_field_mappings() -> None:
    adapter = _make_adapter()
    mappings = [
        FieldMapping(
            form_field=FormField(
                selector="#name",
                label="Full name",
                field_type="text",
                required=True,
            ),
            data_key="identity.full_name",
            value="Liam Frost",
            filled=True,
            error="",
        ),
        FieldMapping(
            form_field=FormField(
                selector="#yoe",
                label="Years of experience",
                field_type="text",
                required=False,
            ),
            data_key="qa.years_experience",
            value="",
            filled=False,
            error="no matching profile field",
        ),
    ]

    adapter._record_fill_details(mappings)
    detail = adapter._last_fill_details
    assert [entry["label"] for entry in detail] == ["Full name", "Years of experience"]
    assert detail[0] == {
        "label": "Full name",
        "data_key": "identity.full_name",
        "value": "Liam Frost",
        "filled": True,
        "error": "",
        "required": True,
        "field_type": "text",
    }
    # The miss preserves the error so the Review UI can surface it.
    assert detail[1]["filled"] is False
    assert detail[1]["error"] == "no matching profile field"


def test_serialize_execution_result_includes_fill_details() -> None:
    """``_serialize_execution_result`` is the bridge from the in-memory
    result object to the JSON payload returned by the apply endpoint.
    A regression here would mean the Review queue still sees empty
    details even after the adapter recorded them correctly."""
    from src.application.jobs import _serialize_execution_result

    result = ApplicationResult(job_id="abc123")
    result.fields_filled = 2
    result.fields_total = 3
    result.fill_details = [
        {
            "label": "Email",
            "data_key": "identity.email",
            "value": "liam@example.com",
            "filled": True,
            "error": "",
        }
    ]

    serialised = _serialize_execution_result(result)
    assert serialised["fill_details"] == result.fill_details

    # ``None`` result still has the key so the UI never KeyErrors.
    none_payload = _serialize_execution_result(None)
    assert none_payload["fill_details"] == []


def test_generic_adapter_apply_copies_fill_details_to_result() -> None:
    """GenericAdapter overrides ``apply()`` and used to skip the
    "copy ``_last_fill_details`` onto ``result.fill_details``" step
    that the base ``apply()`` runs. Verify the fix at the source level
    so a future refactor cannot silently regress -- we look for the
    explicit assignment on the result inside ``apply``."""
    import importlib
    import textwrap

    src = importlib.import_module("src.execution.ats.generic").__file__
    assert src is not None
    body = open(src, encoding="utf-8").read()
    # Pin the contract: ``apply()`` must assign result.fill_details
    # from the per-page accumulator before returning.
    assert "result.fill_details = list(self._last_fill_details)" in textwrap.dedent(body), (
        "GenericAdapter.apply() must copy self._last_fill_details onto "
        "result.fill_details so company_site / workday applies persist "
        "per-field details."
    )
    # And it must reset the accumulator at the start of the form walk
    # so a re-used adapter instance does not leak details across calls.
    assert "self._last_fill_details = []" in body, (
        "GenericAdapter.apply() must reset self._last_fill_details before "
        "iterating pages to avoid leaking details across applications."
    )
