# Webapp UI contract

Approved by the owner on 2026-10-15 as the target look and layout. The static mockups in [`ui-contract/`](ui-contract/) (`Main.html` Chat, `Portfolio.html`, `Setup.html`; open them in a browser) are the contract: the built UI must match their structure, hierarchy, copy tone and states. `DESIGN.md` still supplies tokens; where the mockups and `DESIGN.md` disagree, the mockups win.

## Principles
1. **Chat is home.** The owner talks to the agent; every other page supports that.
2. **Light, calm, fintech.** Off-white ground (`#FAFAF6`), white cards with a 1 px warm border, 16 px radius, bright yellow accent (`#FFD23F`, soft `#FFF3BF` for active tabs and `#FFE680` for owner bubbles), green for done or safe, red only for blocked or stop. No dark theme by default (dark mode may follow `prefers-color-scheme`, same structure).
3. **No warning banner.** No full-width notice bar on any page. Testnet and custody are stated once in Setup (first run), by a small chip "Base Sepolia testnet" in the header, and by one grey line in the footer. The honest content is unchanged, only its placement.
4. **Plain words first.** Every outcome is a sentence a non-technical person understands; rule codes are small secondary chips; transaction hashes only as "BaseScan" links.
5. **Provenance stays honest.** "Agent says" text is labelled; step cards come from the audit log only; reference prices are labelled where a USD value is shown.

## Shell (all pages)
Header 64 px, white, bottom border: logo mark and name, tabs **Chat · Portfolio · Activity · Rules**, then (right) network chip, owner address chip, status pill ("Agent active" green, "Read only" amber, "Stopped" red), **Emergency stop** (outline red button; when stopped it becomes a high-contrast "Resume" flow needing a signature). Content max width 1280 px, 32 px side padding; below 900 px the right column stacks under the chat and the tabs scroll.

## Screens
| Screen | Contract |
| :--- | :--- |
| **Chat** (`Main.html`) | Left: conversation card with composer. Owner bubbles right (yellow), agent bubbles left (grey), then step cards: done (green), blocked (red, with plain reason and rule chip), pending approval (amber, Approve and Reject), skipped (grey). Suggested prompt chips above the composer. Typing state while a job runs. Right column: wallet value and idle/earning split bar, today's limit bar (rolling 24 h, approval threshold), gas level with Top up button, audit log line with verified state. |
| **Portfolio** (`Portfolio.html`) | Total value, allocation bar with legend, earning rate, holdings table (USDC, aUSDC, ETH gas) with Withdraw and Top up, recent activity list, waiting-for-approval card. |
| **Setup** (`Setup.html`) | Shown until activated: three steps (Connected, Add gas, Activate), agent address with copy, gas amount and send button, Activate with its one-sentence explanation and signature. After activation the user lands in Chat. Mint test USDC lives on Portfolio. |
| **Activity** | Feed of every decision, newest first, same step-card vocabulary as Chat, with verify-chain action and result. |
| **Rules** | Read-only summary of the owner's limits (per transaction, per day, approval threshold, allowed protocols) from the existing API; if no route exposes them the tab is hidden, not faked. |

## Acceptance
- Each screen renders at 1280 px and at 375 px without horizontal scroll; keyboard operable; visible focus.
- No `localStorage`, no external URL except BaseScan and the wallet, no new dependency.
- The previous full-width warning banner is gone; the tests that asserted it are replaced by tests that assert the chip, the footer line and the Setup text.
- Screenshots of the built screens are committed next to the mockups for side-by-side review.
