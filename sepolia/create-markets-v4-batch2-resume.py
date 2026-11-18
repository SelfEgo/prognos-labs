# Resume batch 2: create markets 3 and 4 on the v4 factory.
# The first run created markets 1 and 2, then crashed on market 3:
# gas_limit = estimate*2 (~19.6M) at maxFee 1 gwei needs 0.0196 ETH upfront,
# but the wallet held only 0.0159 ETH. Fix: gas_limit = max(estimate*1.2, 12M)
# and maxFee 0.6 gwei (Sepolia effective price observed ~0.5 gwei).
# Testnet only, chain 11155111. Never logs the key. Never resolves a market.
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python create-markets-v4-batch2-resume.py
import json, os, time
import solcx
from web3 import Web3
from eth_account import Account

BASE = os.path.dirname(os.path.abspath(__file__))
RPC = "https://ethereum-sepolia-rpc.publicnode.com"
OUT = f"{BASE}/sepolia-markets-v4-batch2.json"
FACTORY_ADDR = "0x9F0975e862B57cC34Fb62aB9f7F58d368D6DE4Ef"
RESERVE_ETH = 0.005
EST_COST_PER_CREATE_ETH = 0.006

# Markets 1-2 already created by the first run (txs confirmed status 1).
ALREADY = [
    {"index": 1, "key": "Arsenal vs Man City", "category": "Sports",
     "question": "Will Arsenal beat Manchester City on 29 November 2026?", "expiry": 1796079540,
     "market_address": "0x75033182673f578d8018FF25523D90A4DEB3f36b",
     "tx_hash": "86bb510dbad11328928bb112a31fb8631e144e2bc57058a03b5b8f698909428a",
     "gas_used": 9720358, "verified": False,
     "factory_market_count_after": 6,
     "note": "question/expiry/resolved matched on-chain in run 1; count/list read was stale (lagging node)"},
    {"index": 2, "key": "Gambia Barrow", "category": "Politics",
     "question": "Will Adama Barrow win re-election as President of The Gambia on 5 December 2026?", "expiry": 1796597940,
     "market_address": "0x14Bbb22df7129447cA075D3c5136b8B2Dea9b1Ad",
     "tx_hash": "99e0a8b1b4ac4baf5bab2450dcb687cafff7b80fed179b86565343839265d30f",
     "gas_used": 9831255, "verified": True,
     "factory_market_count_after": 7},
]

MARKETS = [
    ("GTA VI GOTY win", "Entertainment",
     "Will GTA VI win Game of the Year at The Game Awards 2026?", 1797029940),
    ("BoE Dec hike", "Economics",
     "Will the Bank of England raise Bank Rate on 17 December 2026?", 1797634740),
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

def call_with_retry(fn, tries=15, sleep=5):
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
    gas_limit = max(int(est * 1.2), gas_floor)
    print(f"  estimate={est} gas_limit={gas_limit}", flush=True)
    h = None
    for attempt in range(5):
        tx = fn.build_transaction({
            "from": deployer, "nonce": w3.eth.get_transaction_count(deployer, "pending"), "value": value,
            "gas": gas_limit,
            "maxFeePerGas": w3.to_wei(0.6, "gwei"),
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
print(f"factory marketCount before resume: {count0}", flush=True)

created = list(ALREADY)
skipped = []
for j, (key, category, question, expiry) in enumerate(MARKETS, 3):
    bal_now = float(w3.from_wei(w3.eth.get_balance(deployer), "ether"))
    if bal_now - EST_COST_PER_CREATE_ETH < RESERVE_ETH:
        print(f"STOP market {j} ({key}): balance {bal_now:.6f} ETH would drop below {RESERVE_ETH} ETH reserve.", flush=True)
        skipped.append({"index": j, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": f"reserve guard: balance {bal_now:.6f} ETH"})
        for key2, cat2, q2, e2 in MARKETS[j - 3:]:
            skipped.append({"index": MARKETS.index((key2, cat2, q2, e2)) + 3, "key": key2, "category": cat2,
                            "question": q2, "expiry": e2, "reason": "skipped after reserve guard stopped earlier market"})
        break
    if expiry <= int(time.time()) + 60:
        print(f"SKIP market {j} ({key}): expiry not in future", flush=True)
        skipped.append({"index": j, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": "expiry not in future"})
        continue
    print(f"Creating market {j} ({key}) [{category}]: {question} expiry={expiry}", flush=True)
    txh, rcpt = send(factory.functions.createMarket(question, expiry, deployer))
    print(f"  tx={txh} status={rcpt.status} gasUsed={rcpt.gasUsed}", flush=True)
    if rcpt.status != 1:
        skipped.append({"index": j, "key": key, "category": category, "question": question, "expiry": expiry,
                        "reason": f"on-chain tx failed status={rcpt.status} tx={txh}"})
        continue
    logs = factory.events.MarketCreated().process_receipt(rcpt)
    market_addr = logs[0]["args"]["market"] if logs else None
    created.append({"index": j, "key": key, "category": category, "question": question, "expiry": expiry,
                    "market_address": market_addr, "tx_hash": txh, "gas_used": rcpt.gasUsed, "verified": False,
                    "factory_market_count_after": None})

# Final verification: poll until the chain state catches up (publicnode lags),
# then verify every new market with fresh reads.
print("Final verification: polling marketCount until it reaches the expected total...", flush=True)
expected = 6 + len([c for c in created if c.get("market_address")])
count_f = None
markets_f = []
for attempt in range(40):
    try:
        count_f = factory.functions.marketCount().call()
        markets_f = factory.functions.getMarkets().call()
    except Exception as e:
        print(f"  final poll {attempt}: {str(e)[:80]}", flush=True)
        time.sleep(5)
        continue
    print(f"  final poll {attempt}: count={count_f} markets={len(markets_f)}", flush=True)
    if count_f == expected and len(markets_f) == expected:
        break
    time.sleep(5)

for c in created:
    addr = c.get("market_address")
    if not addr:
        continue
    market = w3.eth.contract(address=addr, abi=compiled["PrognosMarket"])
    try:
        q = call_with_retry(market.functions.question())
        ex = call_with_retry(market.functions.expiry())
        res = call_with_retry(market.functions.resolved())
    except Exception as e:
        print(f"  VERIFY {c['key']}: read failed after retries: {str(e)[:100]}", flush=True)
        continue
    ok = (q == c["question"] and ex == c["expiry"] and res is False and addr in markets_f)
    c["verified"] = ok
    c["factory_market_count_after"] = count_f
    print(f"  VERIFY {c['key']} market={addr} count={count_f} question_match={q == c['question']} expiry_match={ex == c['expiry']} resolved={res} in_list={addr in markets_f} -> {'OK' if ok else 'MISMATCH'}", flush=True)

bal_f = w3.eth.get_balance(deployer)
nonce_f = w3.eth.get_transaction_count(deployer, "pending")
print(f"FINAL factory marketCount={count_f} markets={markets_f}", flush=True)
print(f"FINAL wallet balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}", flush=True)

with open(OUT, "w") as f:
    json.dump({
        "status": "OK" if len(created) == 4 and all(c.get("verified") for c in created) else "PARTIAL",
        "network": "Sepolia",
        "chain_id": 11155111,
        "version": "v4 difference curve",
        "deployer": deployer,
        "factory_address": FACTORY_ADDR,
        "factory_market_count_before": 6,
        "factory_market_count": count_f,
        "markets": markets_f,
        "created": created,
        "skipped": skipped,
        "wallet_balance_eth": str(w3.from_wei(bal_f, "ether")),
        "wallet_nonce": nonce_f,
    }, f, indent=1)
print(f"wrote {OUT}", flush=True)
