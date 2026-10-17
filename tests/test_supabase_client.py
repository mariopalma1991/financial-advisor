"""pg_connection URL handling: unencoded special characters in the password."""
from src.storage.supabase_client import _quote_password


def test_password_with_at_sign_is_encoded():
    url = "postgresql://postgres.abc:p@ss@22@aws-1-x.pooler.supabase.com:6543/postgres"
    assert _quote_password(url) == (
        "postgresql://postgres.abc:p%40ss%4022@aws-1-x.pooler.supabase.com:6543/postgres"
    )


def test_plain_and_already_encoded_urls_are_unchanged():
    plain = "postgresql://postgres.abc:secret@host:5432/postgres"
    encoded = "postgresql://postgres.abc:p%40ss@host:5432/postgres"
    assert _quote_password(plain) == plain
    assert _quote_password(encoded) == encoded
