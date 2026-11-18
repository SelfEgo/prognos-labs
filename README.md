# Prognos Labs / Prognosis

Prognosis is a prediction market with AI models that learn from market outcomes. Every market asks one question about what happens next, with two outcomes. The price shows the chance traders give each outcome, and every market carries a forecast with a plain-language reason for it. When a market settles on the real outcome, the forecast it gave is scored against that outcome, so the next forecast is sharper than the last.

## What is in this repo

- `website/` - the prediction market website. It shows live markets, the forecast on every market, price alerts, market news, and a watchlist. Most markets shown here come from Gate. Trading is not open yet.
- `sepolia/contracts/` - the market contracts, version 4. `MarketFactoryV4.sol` creates markets. `PrognosMarketV4.sol` is the market itself, a simple binary Yes/No market with parimutuel settlement.
- `sepolia/` - the deploy scripts for the version 4 contracts.

## Platform contract

The platform contract is the Sepolia version 4 market factory:

`0x9F0975e862B57cC34Fb62aB9f7F58d368D6DE4Ef`

No individual market addresses are listed here. Most markets on the site come from Gate, and only a few are ours.

## Test contracts only

The contracts in this repo are TEST contracts on Sepolia (chain id 11155111). They hold no real value. They are unaudited and for testing only. Never deploy them with real value, and never use them on mainnet.

## Running the website

The website is plain PHP and HTML. Put the contents of `website/` in a web folder with PHP, and open `index.html`. The `api.php` file fetches live market data and records forecasts for scoring. The `cache/` folder is blocked from the web by its `.htaccess` file, so learning records and price caches are never readable over the web. Do not remove that block.

## Running the deploy scripts

The scripts need Python 3 with `web3` and `py-solc-x`, plus solc 0.8.24. They talk to Sepolia through a public RPC endpoint and need a test wallet with Sepolia ETH test funds.

Each script reads its private key from a file called `test-wallet.key` sitting next to the script. That file is testnet only and is never committed to this repo. Create it yourself with a fresh test key, in the form `private_key=<your test key>`, and keep it out of version control.

The scripts find their own folder, so they work from a fresh checkout. They read `test-wallet.key` and write their result files next to themselves.

## How markets settle

A market settles on the real outcome of the event its question describes. A person confirms the outcome. The forecast never creates a market and never settles one.

## License

MIT. See the SPDX headers in the contracts.
