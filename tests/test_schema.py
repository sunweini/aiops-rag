"""Test frontmatter and topology schema validation."""
from app.schema import validate_frontmatter, VALID_DOC_TYPES


def test_rejects_empty_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": [],
    })
    assert len(errors) > 0, "Empty service_ids should be rejected"


def test_rejects_missing_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
    })
    assert len(errors) > 0, "Missing service_ids should be rejected"


def test_accepts_valid_service_ids():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": ["svc_nginx", "svc_order"],
        "tags": ["502"],
        "updated_at": "2026-06-02",
    })
    assert len(errors) == 0, f"Valid frontmatter should pass, got: {errors}"


def test_rejects_non_svc_prefix():
    errors = validate_frontmatter({
        "title": "Test Doc",
        "doc_type": "sop",
        "service_ids": ["not_a_service"],
    })
    assert len(errors) > 0, "Non svc_ prefix should be rejected"


def test_valid_doc_types():
    for dt in ["sop", "tech", "incident"]:
        errors = validate_frontmatter({
            "title": "Test",
            "doc_type": dt,
            "service_ids": ["svc_test"],
        })
        assert len(errors) == 0, f"{dt} should be valid"


def test_rejects_invalid_doc_type():
    errors = validate_frontmatter({
        "title": "Test",
        "doc_type": "not_valid",
        "service_ids": ["svc_test"],
    })
    assert len(errors) > 0, "Invalid doc_type should be rejected"
