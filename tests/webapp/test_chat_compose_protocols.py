"""Plain-English step phrases name the protocol, not always Aave."""

from api.chat_compose import action_phrase

AMOUNT = {"value": "5000000", "decimals": 6}


def order(**kw):
    return {"token": "USDC", "amount": AMOUNT, "recipient": "self", **kw}


def test_aave_v3_phrases_are_unchanged():
    o = order(protocol="aave-v3", function="supply")
    assert action_phrase(o, past=True) == "supplied 5 USDC to Aave"
    o = order(protocol="aave-v3", function="approve", recipient="aave_pool")
    assert action_phrase(o, past=True) == "approved 5 USDC for the Aave pool"


def test_other_protocols_are_named():
    assert action_phrase(order(protocol="morpho-blue", function="approve"), past=True) == (
        "approved 5 USDC for Morpho Blue"
    )
    got = action_phrase(
        order(protocol="morpho-blue", function="supply", market="usdc-wbtc"), past=True
    )
    assert got == "supplied 5 USDC to Morpho Blue (USDC/WBTC market)"
    assert "Stata" in action_phrase(order(protocol="aave-stata", function="supply"), past=False)
    swap = action_phrase(
        order(protocol="uniswap-v3", function="exactInputSingle", token_out="WBTC"), past=True
    )
    assert swap.startswith("swapped 5 USDC for WBTC on ")
