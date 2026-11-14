import pytest

from src.powerbi_query_service import PowerBIQueryService


@pytest.mark.parametrize(
    "query, expected_error",
    [
        ("", "DAX query cannot be empty"),
        ("   ", "DAX query cannot be empty"),
        ("ROW(\"Test\", 1)", "A valid DAX query must contain EVALUATE"),
    ],
)
def test_reject_invalid_dax(query, expected_error):
    # Bypass authentication initialization for offline testing.
    service = PowerBIQueryService.__new__(PowerBIQueryService)

    with pytest.raises(ValueError, match=expected_error):
        service.execute_dax(query)

from unittest.mock import Mock


def test_successful_dax_execution(monkeypatch):

    service = PowerBIQueryService.__new__(PowerBIQueryService)

    service.dataset_id = "test-dataset"
    service.api_url = "https://example.invalid/executeQueries"
    service.http = Mock()

    monkeypatch.setattr(
        PowerBIQueryService,
        "_get_access_token",
        lambda self: "offline-test-token"
    )

    response = Mock()
    response.ok = True
    response.json.return_value = {
        "results": [
            {
                "tables": [
                    {
                        "rows": [
                            {"[Net Sales]": 150000}
                        ]
                    }
                ]
            }
        ]
    }

    service.http.post.return_value = response

    result = service.execute_dax(
        'EVALUATE ROW("Net Sales", 150000)'
    )

    assert result["success"] is True
    assert result["row_count"] == 1
    assert result["columns"] == ["[Net Sales]"]
    assert result["rows"][0]["[Net Sales]"] == 150000

    service.http.post.assert_called_once()


from src.powerbi_query_service import PowerBIQueryError


def test_powerbi_http_403(monkeypatch):

    service = PowerBIQueryService.__new__(PowerBIQueryService)

    service.dataset_id = "test-dataset"
    service.api_url = "https://example.invalid/executeQueries"
    service.http = Mock()

    monkeypatch.setattr(
        PowerBIQueryService,
        "_get_access_token",
        lambda self: "offline-test-token"
    )

    response = Mock()
    response.ok = False
    response.status_code = 403
    response.json.return_value = {
        "error": {
            "code": "Forbidden",
            "message": "Access denied"
        }
    }

    service.http.post.return_value = response

    with pytest.raises(PowerBIQueryError, match="HTTP 403") as exc:
        service.execute_dax('EVALUATE ROW("Test", 1)')

    assert exc.value.status_code == 403
    service.http.post.assert_called_once()


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        (
            {"results": []},
            "No query results returned",
        ),
        (
            {"results": [{"error": {"code": "DAX_ERROR"}}]},
            "DAX execution error",
        ),
        (
            {"results": [{"tables": [{"error": {"code": "TABLE_ERROR"}}]}]},
            "Result table error",
        ),
    ],
)
def test_powerbi_query_errors(monkeypatch, payload, expected_error):

    service = PowerBIQueryService.__new__(PowerBIQueryService)
    service.dataset_id = "test-dataset"
    service.api_url = "https://example.invalid/executeQueries"
    service.http = Mock()

    monkeypatch.setattr(
        service,
        "_get_access_token",
        lambda: "offline-test-token",
    )

    response = Mock()
    response.ok = True
    response.json.return_value = payload
    service.http.post.return_value = response

    with pytest.raises(PowerBIQueryError, match=expected_error):
        service.execute_dax('EVALUATE ROW("Test", 1)')

