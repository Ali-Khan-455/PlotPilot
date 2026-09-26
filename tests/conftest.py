import pytest


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    """R14: a test that forgets to pass a FakeClient fails loudly instead of calling the API."""
    import plotpilot.cli

    def refuse():
        raise AssertionError("test tried to build a real Anthropic client")

    monkeypatch.setattr(plotpilot.cli, "make_client", refuse)
    import imagesync.cli
    monkeypatch.setattr(imagesync.cli, "make_client", refuse)


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    """Shared by test_final.py and the Image-Sync tests; test files with their own `cwd` override it."""
    from tests.helpers import write_book
    monkeypatch.chdir(tmp_path)
    write_book(tmp_path)
    return tmp_path
