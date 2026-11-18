# Create 4 more on-chain prediction markets on Sepolia via the v4 factory.
# Testnet only, chain 11155111. Never logs the key. Never resolves a market.
# One per thin category: Sports, Politics, Entertainment, Economics.
# Reserve rule: stop creating if balance would drop below 0.005 ETH.
# Gas: gas_limit = max(estimate * 2, 12_000_000) per the v4 deploy script
# (publicnode lags and under-estimates).
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python create-markets-v4-batch2.py
import json, os, time
import solcx
from web3 import Web3
from eth_account import Account

BASE = os.path.dirname(os.path.abspath(__file__))
RPC = "https://ethereum-sepolia-rpc.publicnode.com"
OUT = f"{BASE}/sepolia-markets-v4-batch2.json"
FACTORY_ADDR = "0x9F0975e862B57cC34Fb62aB9f7F58d368D6DE4Ef"
RESERVE_ETH = 0.005
EST_COST_PER_CREATE_ETH = 0.006  # conservative guard (observed ~0.0047)

# (key, category, question, expiry)
MARKETS = [
    ("Arsenal vs Man City", "Sports",
     "Will Arsenal beat Manchester City on 29 November 2026?", 1796079540),  # Mon 30 Nov 2026 23:59 WAT; match Sun 29 Nov 16:30 UK, Emirates
    ("Gambia Barrow", "Politics",
     "Will Adama Barrow win re-election as President of The Gambia on 5 December 2026?", 1796597940),  # Sun 6 Dec 2026 23:59 WAT; election Sat 5 Dec 2026
    ("GTA VI GOTY win", "Entertainment",
     "Will GTA VI win Game of the Year at The Game Awards 2026?", 1797029940),  # Fri 11 Dec 2026 23:59 WAT; ceremony Thu 10 Dec 2026, Peacock Theater
    ("BoE Dec hike", "Economics",
     "Will the Bank of England raise Bank Rate on 17 December 2026?", 1797634740),  # Fri 18 Dec 2026 23:59 WAT; MPC decision Thu 17 Dec 2026 12:00 UK
]

acct = None
for line in open(f"{BASE}/test-wallet.key"):
    line = line.strip()
    if line.startswith("private_key="):
        acct = Account.from_key(line.split("=", 1)[1])
w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == 11155111
deployer = acct.address
bal = w3.eth.get_balance(deployer)
nonce = w3.eth.get_transaction_count(deployer, "pending")
print(f"deployer={deployer} balance={w3.from_wei(bal, 'ether')} ETH nonce={nonce}", flush=True)

def call_with_retry(fn, tries=12, sleep=5):
    for attempt in range(tries):
        try:
            return fn.call()
        except Exception as e:
            print(f"  call retry {attempt}: {str(e)[:80]}", flush=True)
            time.sleep(sleep)
    return fn.call()

def send(fn, value=0, gas_floor=12_000_000):
    est = None
    for attempt in range(5):
        try:
            est = fn.estimate_gas({"from": deployer, "value": value})
            break
        except Exception as e:
            print(f"  estimate retry {attempt}: {str(e)[:100]}", flush=True)
            time.sleep(5)
    if est is None:
        est = gas_floor
        print(f"  estimate failed after retries, using floor {gas_floor} as estimate", flush=True)
    gas_limit = max(int(est * 2), gas_floor)
    print(f"  estimate={est} gas_limit={gas_limit}", flush=True)
    h = None
    for attempt in range(5):
        tx = fn.build_transaction({
            "from": deployer, "nonce": w3.eth.get_transaction_count(deployer, "pending"), "value": value,
            "gas": gas_limit,
            "maxFeePerGas": w3.to_wei(1, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.5, "gwei"),
            "chainId": 11155111,
        })
        signed = acct.sign_transaction(tx)
        try:
            h = w3.eth.send_raw_transaction(signed.raw_transaction)
            break
        except Exception as e:
            if "nonce too low" in str(e).lower() or "already known" in str(e).lower():
                print(f"  send retry {attempt} (nonce): {str(e)[:100]}", flush=True)
                time.sleep(5)
                continue
            raise
    if h is None:
        raise RuntimeError("send_raw_transaction failed after 5 attempts")
    try:
        rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=300)
    except Exception:
        rcpt = None
        for _ in range(60):
            time.sleep(5)
            try:
                rcpt = w3.eth.get_transaction_receipt(h)
                if rcpt is not None:
                    break
            except Exception:
                pass
        if rcpt is None:
            raise RuntimeError(f"no receipt for {h.hex()} after polling")
    return h.hex(), rcpt

compiled = {}
for name, fname in [("PrognosMarket", "PrognosMarketV4"), ("MarketFactory", "MarketFactoryV4")]:
    c = solcx.compile_files([f"{BASE}/contracts/{fname}.sol"], output_values=["abi", "bin"],
                            solc_version="0.8.24", optimize=True, optimize_runs=200)
    key = next(k for k in c if k.endswith(f":{name}"))
    compiled[name] = c[key]["abi"]

factory = w3.eth.contract(address=FACTORY_ADDR, abi=compiled["MarketFactory"])
count0 = call_with_retry(factory.functions.marketCount())
print(f"factory marketCount before: {count0}", flush=True)

created = []
skipped = []
for i, (key, category, question, expiry) in enumerate(MARKETS, 1):
    bal_now = float(w3.from_wei(w3.eth.get_balance(deployer), "ether"))
    if bal_now - EST_COST_PER_CREATE_ETH < RESERVE_ETH:
        print(f"STOP market {i} ({key}): balance {bal_now:.6f} ETH would drop below {RESERVE_ETH} ETH reserve. Skipping remaining.", flush=True)
        skipped.append({"index": i, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": f"reserve guard: balance {bal_now:.6f} ETH"})
        for key2, cat2, q2, e2 in MARKETS[i:]:
            skipped.append({"index": MARKETS.index((key2, cat2, q2, e2)) + 1, "key": key2, "category": cat2,
                            "question": q2, "expiry": e2, "reason": "skipped after reserve guard stopped earlier market"})
        break
    if expiry <= int(time.time()) + 60:
        print(f"SKIP market {i} ({key}): expiry {expiry} not in future", flush=True)
        skipped.append({"index": i, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": "expiry not in future"})
        continue
    print(f"Creating market {i} ({key}) [{category}]: {question} expiry={expiry}", flush=True)
    txh, rcpt = send(factory.functions.createMarket(question, expiry, deployer))
    print(f"  tx={txh} status={rcpt.status} gasUsed={rcpt.gasUsed}", flush=True)
    if rcpt.status != 1:
        skipped.append({"index": i, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": f"on-chain tx failed status={rcpt.status} tx={txh}"})
        continue
    logs = factory.events.MarketCreated().process_receipt(rcpt)
    market_addr = logs[0]["args"]["market"] if logs else None
    count = call_with_retry(factory.functions.marketCount())
    markets = call_with_retry(factory.functions.getMarkets())
    market = w3.eth.contract(address=market_addr, abi=compiled["PrognosMarket"])
    q = call_with_retry(market.functions.question())
    ex = call_with_retry(market.functions.expiry())
    res = call_with_retry(market.functions.resolved())
    ok = (q == question and ex == expiry and res is False and market_addr in markets)
    print(f"  VERIFY market={market_addr} count={count} question_match={q == question} expiry_match={ex == expiry} resolved={res} in_list={market_addr in markets} -> {'OK' if ok else 'MISMATCH'}", flush=True)
    created.append({"index": i, "key": key, "category": category, "question": question, "expiry": expiry,
                    "market_address": market_addr, "tx_hash": txh, "gas_used": rcpt.gasUsed, "verified": ok,
                    "factory_market_count_after": count})

count_f = call_with_retry(factory.functions.marketCount())
markets_f = call_with_retry(factory.functions.getMarkets())
bal_f = w3.eth.get_balance(deployer)
nonce_f = w3.eth.get_transaction_count(deployer, "pending")
print(f"FINAL factory marketCount={count_f} markets={markets_f}", flush=True)
print(f"FINAL wallet balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}", flush=True)

with open(OUT, "w") as f:
    json.dump({
        "status": "OK" if len(created) == len(MARKETS) else "PARTIAL",
        "network": "Sepolia",
        "chain_id": 11155111,
        "version": "v4 difference curve",
        "deployer": deployer,
        "factory_address": FACTORY_ADDR,
        "factory_market_count_before": count0,
        "factory_market_count": count_f,
        "markets": markets_f,
        "created": created,
        "skipped": skipped,
        "wallet_balance_eth": str(w3.from_wei(bal_f, "ether")),
        "wallet_nonce": nonce_f,
    }, f, indent=1)
print(f"wrote {OUT}", flush=True)
