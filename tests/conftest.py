import pytest


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    """R14: a test that forgets to pass a FakeClient fails loudly instead of calling the API."""
    import plotpilot.cli

    def refuse():
        raise AssertionError("test tried to build a real Anthropic client")

    monkeypatch.setattr(plotpilot.cli, "make_client", refuse)
