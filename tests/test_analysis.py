import json

import httpx
import pytest
from pydantic import ValidationError

from papertrail.analysis import Commitment, DocumentAnalysis, Topic, route_for
from papertrail.ollama import OllamaAnalyzer, select_analysis_text
from papertrail.worker import ProcessingError


def analysis_data(**changes):
    data = {
        "topic": "security",
        "document_type": "fact_sheet",
        "language": "en",
        "summary": "The document explains phishing controls.",
        "keywords": ["phishing", "email", "security"],
        "actionability": "advisory",
        "entities": [],
        "commitments": [],
    }
    return data | changes


def test_routes_actionable_and_security_documents():
    required = DocumentAnalysis.model_validate(
        analysis_data(actionability="required_action")
    )
    advisory = DocumentAnalysis.model_validate(analysis_data())

    assert route_for(required) == "action_queue"
    assert route_for(advisory) == "review_queue"


def test_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        DocumentAnalysis.model_validate(analysis_data(unexpected="value"))


def test_selection_leaves_short_markdown_unchanged():
    source = "# Safety guide\n\nOperators should inspect the filter daily."

    selection = select_analysis_text(source)

    assert selection.text == source
    assert selection.source_characters == len(source)
    assert selection.selected_characters == len(source)
    assert selection.compacted is False


def test_selection_compacts_long_markdown_without_splitting_or_inventing_blocks():
    action = "The facility shall inspect every treatment unit before startup."
    paragraphs = [
        "# Remediation plan",
        "This overview describes the cleanup and its environmental objectives.",
        *[f"Background section {index} " + "detail " * 35 for index in range(30)],
        action,
        "Final monitoring results are reported to the project team.",
        "The report closes with contact information for the agency.",
    ]
    source = "\n\n".join(paragraphs)

    selection = select_analysis_text(source)

    assert selection.compacted is True
    assert len(json.dumps(selection.text, ensure_ascii=False).encode()) <= 5600
    assert action in selection.text
    assert selection.text.startswith("# Remediation plan")
    assert selection.text.endswith(paragraphs[-1])
    assert all(block in source for block in selection.text.split("\n\n"))


def test_selection_guarantees_serialized_size_for_multibyte_paragraphs():
    action = "Operators should reduce emissions before work begins."
    source = "\n\n".join(["# Guidance", *(["界" * 700] * 8), action, "Final note."])

    selection = select_analysis_text(source)

    assert len(json.dumps(selection.text, ensure_ascii=False).encode("utf-8")) <= 5600
    assert action in selection.text


@pytest.mark.parametrize(
    "source",
    [
        "A sentence with useful context. " + "a" * 10_000,
        "Ważne zalecenie dla użytkownika. " + "界" * 10_000,
    ],
)
def test_selection_falls_back_to_safe_prefix_for_one_oversized_paragraph(source):
    selection = select_analysis_text(source)

    assert selection.text
    assert len(json.dumps(selection.text, ensure_ascii=False).encode("utf-8")) <= 5600
    assert source.startswith(selection.text)
    assert selection.source_characters == len(source)
    assert selection.selected_characters == len(selection.text)
    assert selection.compacted is True


@pytest.mark.asyncio
async def test_analyzer_accepts_oversized_single_paragraph_selection():
    selection = select_analysis_text("A security guidance sentence. " + "界" * 10_000)

    async def handle(request):
        payload = json.loads(request.content)
        encoded_document = payload["messages"][-1]["content"].split("\n", 1)[1]
        assert json.loads(encoded_document) == selection.text
        return httpx.Response(200, json={"message": {"content": json.dumps(analysis_data())}})

    analyzer = OllamaAnalyzer(transport=httpx.MockTransport(handle))
    run = await analyzer.analyze(selection.text)

    assert run.analysis.topic == Topic.SECURITY


@pytest.mark.asyncio
async def test_sends_a_structured_ollama_request():
    source = "A short guide to phishing controls."

    async def handle(request):
        payload = json.loads(request.content)
        encoded_document = payload["messages"][-1]["content"].split("\n", 1)[1]
        assert request.url.path == "/api/chat"
        assert payload["stream"] is False
        assert payload["format"]["type"] == "object"
        assert json.loads(encoded_document) == source
        body = {
            "message": {"content": json.dumps(analysis_data())},
            "prompt_eval_count": 120,
            "eval_count": 40,
        }
        return httpx.Response(200, json=body)

    analyzer = OllamaAnalyzer(transport=httpx.MockTransport(handle))
    run = await analyzer.analyze(source)
    assert run.analysis.topic == Topic.SECURITY
    assert run.route == "review_queue"
    assert run.prompt_tokens == 120


def test_drops_commitment_fields_not_supported_by_evidence():
    source = "The agency must file the report by Friday."
    result = DocumentAnalysis.model_validate(
        analysis_data(
            actionability="required_action",
            commitments=[
                Commitment(
                    action="file the report", owner="US", deadline="Friday", evidence=source
                )
            ],
        )
    )

    grounded = OllamaAnalyzer._ground(result, source)
    assert grounded.commitments == []


def test_keeps_commitment_supported_by_evidence():
    source = "Acme shall file the report by Friday."
    commitment = Commitment(
        action="file the report", owner="Acme", deadline="Friday", evidence=source
    )
    result = DocumentAnalysis.model_validate(
        analysis_data(actionability="required_action", commitments=[commitment])
    )

    grounded = OllamaAnalyzer._ground(result, source)
    assert grounded.commitments == [commitment]


@pytest.mark.asyncio
async def test_rejects_token_dense_input_before_request():
    async def handle(request):
        raise AssertionError("Ollama request should not be sent")

    analyzer = OllamaAnalyzer(max_input_bytes=10_000, transport=httpx.MockTransport(handle))
    with pytest.raises(ProcessingError) as error:
        await analyzer.analyze("界" * 2500)

    assert error.value.failure.code == "analysis_input_too_large"


@pytest.mark.asyncio
async def test_retries_invalid_model_output_once():
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": "{}"}})

    analyzer = OllamaAnalyzer(transport=httpx.MockTransport(handle))
    with pytest.raises(ProcessingError) as error:
        await analyzer.analyze("Some document text.")

    assert calls == 2
    assert error.value.failure.code == "ollama_invalid_output"
