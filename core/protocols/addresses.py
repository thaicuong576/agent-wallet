"""Contract addresses and enum-to-address mappings for Base Sepolia (Chain ID 84532).

Addresses are verified on-chain against Base Sepolia RPC and sourced from primary
documentation documented in docs/design/testnet-facts-aave.md.
"""

from typing import Final

from web3 import Web3

# Base Sepolia Chain ID
BASE_SEPOLIA_CHAIN_ID: Final[int] = 84532

# Aave V3 Pool Proxy on Base Sepolia
# Source: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L16-L17
AAVE_V3_POOL: Final[str] = "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27"

# Aave Testnet Mock USDC Underlying on Base Sepolia (NOT Circle native USDC)
# Source: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L88-L91
AAVE_V3_USDC: Final[str] = "0xba50Cd2A20f6DA35D788639E581bca8d0B5d4D5f"

# Aave V3 PoolAddressesProvider on Base Sepolia
# Source: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L11-L13
AAVE_V3_POOL_ADDRESSES_PROVIDER: Final[str] = "0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00"

# Aave Base Sepolia aUSDC (aToken)
# Source: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3BaseSepolia.sol#L93-L94
AAVE_V3_A_USDC: Final[str] = "0x10F1A9D11CDf50041f3f8cB7191CBE2f31750ACC"

# Aave Testnet Faucet on Base Sepolia
# Source: https://github.com/bgd-labs/aave-address-book/blob/main/src/MiscBaseSepolia.sol#L12-L13
AAVE_V3_FAUCET: Final[str] = "0xD9145b5F45Ad4519c7ACcD6E0A4A82e83bB8A6Dc"

# Protocol enum to address mapping
PROTOCOL_ADDRESSES: Final[dict[str, str]] = {
    "aave-v3": AAVE_V3_POOL,
}

# Token enum to address mapping
TOKEN_ADDRESSES: Final[dict[str, str]] = {
    "USDC": AAVE_V3_USDC,
}

SUPPORTED_PROTOCOLS: Final[frozenset[str]] = frozenset({"aave-v3"})
SUPPORTED_TOKENS: Final[frozenset[str]] = frozenset({"USDC"})
SUPPORTED_FUNCTIONS: Final[frozenset[str]] = frozenset({"approve", "supply", "withdraw"})
SUPPORTED_RECIPIENTS: Final[frozenset[str]] = frozenset({"self", "aave_pool"})


def resolve_recipient(recipient: str, wallet_address: str) -> str:
    """Resolve an order recipient enum to an on-chain address.

    Args:
        recipient: Order recipient enum ('self' or 'aave_pool').
        wallet_address: Wallet address passed in by the caller.

    Returns:
        Checksummed recipient address string.

    Raises:
        ValueError: If recipient is not supported or wallet_address is invalid.
    """
    if recipient == "self":
        if not wallet_address or not isinstance(wallet_address, str):
            raise ValueError(
                f"Valid wallet address is required when recipient is 'self', got '{wallet_address}'"
            )
        if not Web3.is_address(wallet_address):
            raise ValueError(
                f"Valid wallet address is required when recipient is 'self', got '{wallet_address}'"
            )
        return Web3.to_checksum_address(wallet_address)
    if recipient == "aave_pool":
        return AAVE_V3_POOL
    raise ValueError(f"Unsupported recipient enum for Aave adapter: '{recipient}'")
