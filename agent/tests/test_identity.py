import pytest

from countercharge_agent.identity import NotConfiguredGmailIdentityProvider


def test_not_configured_provider_raises_not_implemented():
    provider = NotConfiguredGmailIdentityProvider()

    with pytest.raises(NotImplementedError):
        provider.get_access(user_token="whatever")
