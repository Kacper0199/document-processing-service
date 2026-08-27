import json

import httpx
import pytest
from pydantic import ValidationError

from papertrail.analysis import Commitment, DocumentAnalysis, Topic, route_for
from papertrail.ollama import OllamaAnalyzer
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

    analyzer = OllamaAnalyzer(transport=httpx.MockTransport(handle))
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
