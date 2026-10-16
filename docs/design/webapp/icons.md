# Token and protocol icons

## Purpose

The web UI shows token and protocol icons (`ui/icons.js`, styled by `ui/icons.css`). They are token and protocol marks only. Nothing is loaded from the internet at runtime: every token image is a local SVG file under `ui/assets/tokens/`, and protocol marks are letter monograms drawn in code.

## Sources

| File | Source URL | Commit | Licence | Fetched |
| --- | --- | --- | --- | --- |
| `ui/assets/tokens/eth.svg` | `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/svg/color/eth.svg` | `1a63530be6e374711a8554f31b17e4cb92c25fa5` | CC0-1.0 | 2026-10-16 |
| `ui/assets/tokens/usdc.svg` | `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/svg/color/usdc.svg` | `1a63530be6e374711a8554f31b17e4cb92c25fa5` | CC0-1.0 | 2026-10-16 |
| `ui/assets/tokens/usdt.svg` | `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/svg/color/usdt.svg` | `1a63530be6e374711a8554f31b17e4cb92c25fa5` | CC0-1.0 | 2026-10-16 |
| `ui/assets/tokens/wbtc.svg` | `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/svg/color/wbtc.svg` | `1a63530be6e374711a8554f31b17e4cb92c25fa5` | CC0-1.0 | 2026-10-16 |
| `ui/assets/tokens/link.svg` | `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/svg/color/link.svg` | `1a63530be6e374711a8554f31b17e4cb92c25fa5` | CC0-1.0 | 2026-10-16 |

Licence note: the repository's LICENSE.md is CC0 1.0 Universal (`https://raw.githubusercontent.com/spothq/cryptocurrency-icons/1a63530be6e374711a8554f31b17e4cb92c25fa5/LICENSE.md`). CC0 covers the files, but not the owners' trademark rights. So the icons only identify which token a row is. They are not an endorsement by any token issuer, and they are not a logo for the product.

## Token mapping

Copied from `TOKEN_META` in `ui/icons.js`. Every token here is a Base Sepolia test token.

| Symbol | Plain name | Image used |
| --- | --- | --- |
| USDC | USD Coin (test token) | `usdc.svg` |
| USDT | Tether (test token) | `usdt.svg` |
| ETH | Ether (test ETH) | `eth.svg` |
| WETH | Wrapped Ether (test token) | `eth.svg` plus a "W" badge |
| WBTC | Wrapped Bitcoin (test token) | `wbtc.svg` |
| LINK | Chainlink (test token) | `link.svg` |
| cbETH | Coinbase Wrapped Staked ETH (test token) | letter avatar "C" (no file) |
| aUSDC | USDC supplied on Aave | `usdc.svg` plus a protocol badge |
| aUSDT | USDT supplied on Aave | `usdt.svg` plus a protocol badge |
| stataUSDC | USDC in the Aave vault | `usdc.svg` plus a protocol badge |
| stataUSDT | USDT in the Aave vault | `usdt.svg` plus a protocol badge |

## Decisions

- WETH uses the ETH image plus a "W" badge, so the two do not look the same.
- cbETH, and any symbol without a file, uses a letter avatar.
- Derived tokens (aUSDC, stata shares) reuse the base token's image plus a protocol badge.
- Every Base Sepolia token carries the "T" testnet mark, with the tooltip "Test token on Base Sepolia. Not the real asset." Aave's test USDC is not Circle USDC, so the mark is shown on every token.
- Fallback avatar: the FNV-1a 32-bit hash of the symbol picks one of eight palette colours. Each colour is at least 4.5:1 against white. The same symbol always gets the same colour.
- Protocol marks are plain monograms (A, M, U), never logos. Official marks may be added only by a separate change, after someone has read the owner's brand terms. The Aave, Morpho and Uniswap brand pages were not confirmed.

## Not used, and why

- Trust Wallet assets repository: its MIT licence covers the scripts and docs, not the logo files.
- Uniswap default token list: GPL-3.0.

## How to add a token

1. Add a row to `TOKEN_META` in `ui/icons.js`.
2. Add an SVG under `ui/assets/tokens/` only if its licence is confirmed.
3. Add the token to the tables in this file.
4. Run `tests/ui`.
