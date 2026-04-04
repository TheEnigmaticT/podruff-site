import pytest
from unittest.mock import patch, MagicMock
from auth import get_credentials, CREDENTIAL_SETS

def test_get_credentials_unknown_set_raises():
    with pytest.raises(ValueError, match="Unknown credential set"):
        get_credentials("nonexistent")

def test_get_credentials_returns_credentials_object():
    mock_creds = MagicMock()
    mock_creds.valid = True
    with patch("auth._load_token", return_value=mock_creds):
        result = get_credentials("analytics")
    assert result is mock_creds

def test_credential_sets_has_expected_keys():
    assert "analytics" in CREDENTIAL_SETS
    assert "trevor" in CREDENTIAL_SETS
