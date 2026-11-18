# Live cross-side loop (Attack A2 re-test) on the v4 Fed market, plus the
# final verification of the v4 factory and all 6 markets.
# The round trip is already proven on-chain (see sepolia-deploy-v4-proof
# data below, verified from the Buy/Sell events):
#   buy  tx ca83811f36291eb218848b05c3c69041211f3f6762a5a3d22c89ad032bfa7064
#        (block 12138912): 3 Yes, paid 0.002 ETH, fee 0.00002 ETH
#   sell tx 0f24f18fd5bc796d06c07defc4f02d45b2a958ff775d3a76b0ca2264a67f8042
#        (block 12138915): 3 Yes, received 0.0014887125 ETH, fee 0.0000150375 ETH
#   gross 0.00150375 ETH; net = 0.00150375 - 0.00198 = -0.00047625 ETH (LOSS)
#   pool 0 -> 0.00047625 ETH (grew), book back to 0/0.
# This script runs the loop: buy Yes 0.005, buy No 0.005, sell all Yes,
# sell all No. On v3 this loop profited and drained the pool; on v4 it must
# LOSE (only buy dust) and the pool must not shrink.
# Every position read POLLS until the node returns the expected value.
# Testnet only, chain 11155111. Never logs the key. Never resolves a market.
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python sepolia-deploy-v4-loop.py
import json, os, time
import solcx
from web3 import Web3
from eth_account import Account

BASE = os.path.dirname(os.path.abspath(__file__))
RPC = "https://ethereum-sepolia-rpc.publicnode.com"
OUT = f"{BASE}/sepolia-deploy-v4-results.json"
FACTORY_ADDR = "0x9F0975e862B57cC34Fb62aB9f7F58d368D6DE4Ef"
FACTORY_DEPLOY_TX = "491dee3e32d6fb578dc8160c4ab4802238bca7f69c8570cbf71495841affbfd3"
FED_ADDR = "0x0b1D8Af50888028ad9f6c9A5cfF7EDd699b29aB6"
RESERVE_ETH = 0.005

# The 6 v4 markets, in factory order, with their create tx hashes.
MARKETS = [
    ("Fed", "Will the Federal Reserve cut rates in December 2026?", 1795293827,
     "0x0b1D8Af50888028ad9f6c9A5cfF7EDd699b29aB6",
     "6d206c05756f2a91a1940e8adf80f5872504885f706c553fe7c83e81297e802c", 9818250),
    ("Anthropic IPO", "Will Anthropic (ANT) begin trading on Nasdaq by 19 November 2026?", 1795150800,
     "0xEC5c01B4404751507e2b1E220e4dD94f0a03491E",
     "68f704f5bf68877b470108a55153d178a223041a208c1d9614132d4fb9b52c5b", 9831075),
    ("Liverpool vs Man Utd", "Will Liverpool beat Manchester United on 22 November 2026?", 1795388340,
     "0x76BAF55F82bFFd6891C7b14C3525f65048d89050",
     "ec40d94316bb23a68a0a48fc0ab946b9c8692e63b72cc261b6b178d913eac7e9", 9720406),
    ("Spain PP", "Will the People's Party (PP) win the most seats in Spain's general election on 29 November 2026?", 1796079540,
     "0x8bE950d420C3e716e04CFBAbd37fe7FcDF3b5986",
     "2347e753452456eb20b4af48f3430cefc04c25ccdd13259c75ab3ab08e46215c", 9831489),
    ("GTA VI release", "Will GTA VI release on 19 November 2026 as scheduled?", 1795150800,
     "0x8Efa439071fBDAa88AFBEdB238643DA1867b83c6",
     "79950bff846cf7a59d19e3a0ff453a42541815633ffba2b909cf32fb348668bc", 9720346),
    ("GTA VI GOTY", "Will GTA VI be nominated for Game of the Year at The Game Awards 2026?", 1794956340,
     "0x99750A5e6f397C6A08514445505C314586dc7F6F",
     "af2a2e651b1c4a9bcb78a5d64bdec4d067735c5614ff6d03a87cffb7d477d01f", 9831135),
]

def fmt(wei):
    """Signed wei -> ETH string (handles negatives, which from_wei rejects)."""
    sign = "-" if wei < 0 else ""
    return f"{sign}{Web3.from_wei(abs(wei), 'ether')}"

results = []
def log(step, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {step} {detail}", flush=True)
    results.append({"step": step, "result": "PASS" if ok else "FAIL", "detail": detail})

acct = None
for line in open(f"{BASE}/test-wallet.key"):
    line = line.strip()
    if line.startswith("private_key="):
        acct = Account.from_key(line.split("=", 1)[1])
w3 = Web3(Web3.HTTPProvider(RPC))
assert w3.eth.chain_id == 11155111
deployer = acct.address
print(f"deployer={deployer} balance={w3.from_wei(w3.eth.get_balance(deployer), 'ether')} ETH nonce={w3.eth.get_transaction_count(deployer, 'pending')}", flush=True)

def call_with_retry(fn, tries=12, sleep=5):
    for attempt in range(tries):
        try:
            return fn.call()
        except Exception as e:
            print(f"  call retry {attempt}: {str(e)[:80]}", flush=True)
            time.sleep(sleep)
    return fn.call()

def send(fn, value=0, gas_floor=800_000):
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

def wait_position(market, exp_yes, exp_no, timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = market.functions.getPosition(deployer).call()
            if list(last) == [exp_yes, exp_no]:
                return list(last)
        except Exception as e:
            print(f"  wait_position read err: {str(e)[:80]}", flush=True)
        time.sleep(5)
    raise RuntimeError(f"position did not reach [{exp_yes}, {exp_no}] within {timeout}s; last={last}")

def compile_all():
    out = {}
    for name, fname in [("PrognosMarket", "PrognosMarketV4"), ("MarketFactory", "MarketFactoryV4")]:
        compiled = solcx.compile_files(
            [f"{BASE}/contracts/{fname}.sol"], output_values=["abi", "bin"],
            solc_version="0.8.24", optimize=True, optimize_runs=200)
        for key in compiled:
            if key.endswith(f":{name}"):
                out[name] = (compiled[key]["abi"], compiled[key]["bin"])
    return out

abis = compile_all()
factory = w3.eth.contract(address=FACTORY_ADDR, abi=abis["MarketFactory"][0])
market = w3.eth.contract(address=FED_ADDR, abi=abis["PrognosMarket"][0])

# 1. Verify the v4 factory and all 6 markets (fresh reads, poll to catch up).
count = None
markets_f = None
for _ in range(12):
    try:
        count = factory.functions.marketCount().call()
        markets_f = factory.functions.getMarkets().call()
        if count == 6 and len(markets_f) == 6:
            break
    except Exception as e:
        print(f"  factory read err: {str(e)[:80]}", flush=True)
    time.sleep(5)
log("factory v4 marketCount 6", count == 6 and len(markets_f or []) == 6, f"markets={markets_f}")
created = []
for i, (key, question, expiry, addr, txh, gas) in enumerate(MARKETS, 1):
    m = w3.eth.contract(address=addr, abi=abis["PrognosMarket"][0])
    q = call_with_retry(m.functions.question())
    ex = call_with_retry(m.functions.expiry())
    res = call_with_retry(m.functions.resolved())
    ok = (q == question and ex == expiry and res is False and addr in (markets_f or []))
    log(f"verify market {i} ({key}) v4", ok, f"addr={addr} expiry={expiry} resolved={res} in_list={addr in (markets_f or [])}")
    created.append({"index": i, "key": key, "question": question, "expiry": expiry,
                    "market_address": addr, "tx_hash": txh, "gas_used": gas, "verified": ok})

# 2. Confirm the Fed book is back to 0/0 after the round trip.
pos0 = wait_position(market, 0, 0)
pool0 = w3.eth.get_balance(FED_ADDR)
log("Fed v4 book is 0/0 after the round trip", list(pos0) == [0, 0],
    f"pos={list(pos0)} pool={w3.from_wei(pool0, 'ether')} ETH")

# 3. Cross-side loop (Attack A2 re-test): buy Yes 0.005, buy No 0.005,
#    sell all Yes, sell all No. Must LOSE (only buy dust) and the pool
#    must not shrink. Book must return to 0/0.
poolL0 = w3.eth.get_balance(FED_ADDR)
wbL0 = w3.eth.get_balance(deployer)
txh_lb1, rcpt_lb1 = send(market.functions.buyYes(), value=w3.to_wei(0.005, "ether"), gas_floor=800_000)
pos = wait_position(market, 9, 0)
txh_lb2, rcpt_lb2 = send(market.functions.buyNo(), value=w3.to_wei(0.005, "ether"), gas_floor=800_000)
pos = wait_position(market, 9, 10)
log("loop buys", rcpt_lb1.status == 1 and rcpt_lb2.status == 1,
    f"buyYes 9 shares, buyNo 10 shares, pos={list(pos)}")
txh_ls1, rcpt_ls1 = send(market.functions.sellYes(9), gas_floor=800_000)
pos = wait_position(market, 0, 10)
txh_ls2, rcpt_ls2 = send(market.functions.sellNo(10), gas_floor=800_000)
pos_end = wait_position(market, 0, 0)
waL = w3.eth.get_balance(deployer)
poolL1 = w3.eth.get_balance(FED_ADDR)
gas_loop = sum(r.gasUsed * r.effectiveGasPrice for r in [rcpt_lb1, rcpt_lb2, rcpt_ls1, rcpt_ls2])
net_loop = (waL - wbL0) + gas_loop
loop_ok = (all(r.status == 1 for r in [rcpt_lb1, rcpt_lb2, rcpt_ls1, rcpt_ls2])
           and net_loop <= 0 and list(pos_end) == [0, 0] and poolL1 >= poolL0)
log("LIVE cross-side loop Fed (A2 re-test) LOSES money, pool grows, book back to 0/0", loop_ok,
    f"buyYes 9, buyNo 10, net {fmt(net_loop)} ETH, "
    f"pool {w3.from_wei(poolL0, 'ether')}->{w3.from_wei(poolL1, 'ether')} ETH, "
    f"txs buyYes={txh_lb1} buyNo={txh_lb2} sellYes={txh_ls1} sellNo={txh_ls2}")
loop_test = {
    "buy_yes_eth": 0.005, "buy_no_eth": 0.005,
    "shares_bought_yes": 9, "shares_bought_no": 10,
    "shares_sold_yes": 9, "shares_sold_no": 10,
    "net_eth_ex_gas": fmt(net_loop),
    "pool_before_eth": str(w3.from_wei(poolL0, "ether")),
    "pool_after_eth": str(w3.from_wei(poolL1, "ether")),
    "book_after": list(pos_end),
    "tx": {"buy_yes": txh_lb1, "buy_no": txh_lb2, "sell_yes": txh_ls1, "sell_no": txh_ls2},
    "gas": {"buy_yes": rcpt_lb1.gasUsed, "buy_no": rcpt_lb2.gasUsed,
            "sell_yes": rcpt_ls1.gasUsed, "sell_no": rcpt_ls2.gasUsed},
}

# 4. Final state of all 6 v4 markets
final_markets = []
for c in created:
    m = w3.eth.contract(address=c["market_address"], abi=abis["PrognosMarket"][0])
    ys = call_with_retry(m.functions.yesShares())
    ns = call_with_retry(m.functions.noShares())
    pool = w3.eth.get_balance(c["market_address"])
    res = call_with_retry(m.functions.resolved())
    final_markets.append({"key": c["key"], "question": c["question"], "expiry": c["expiry"],
                          "market_address": c["market_address"],
                          "yes_shares": ys, "no_shares": ns,
                          "pool_eth": str(w3.from_wei(pool, "ether")), "resolved": res})
    print(f"  FINAL {c['key']}: Yes {ys} / No {ns}, pool {w3.from_wei(pool, 'ether')} ETH, resolved {res}", flush=True)

bal_f = w3.eth.get_balance(deployer)
nonce_f = w3.eth.get_transaction_count(deployer, "pending")
log("wallet above 0.005 ETH reserve after proof", float(w3.from_wei(bal_f, "ether")) >= RESERVE_ETH,
    f"balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}")

state = {
    "status": "OK" if all(r["result"] == "PASS" for r in results) else "FAILURES",
    "network": "Sepolia", "chain_id": 11155111, "version": "v4 difference curve",
    "deployer": deployer,
    "factory_address": FACTORY_ADDR,
    "deploy_factory_tx": FACTORY_DEPLOY_TX,
    "deploy_factory_gas": 12374093,
    "factory_market_count": count,
    "markets": markets_f,
    "created": created,
    "skipped": [],
    "fed_market_address": FED_ADDR,
    "round_trip": {
        "value_eth": 0.002, "shares_bought": 3, "shares_sold": 3,
        "buy_tx": "ca83811f36291eb218848b05c3c69041211f3f6762a5a3d22c89ad032bfa7064",
        "buy_block": 12138912, "buy_paid_eth": "0.002", "buy_fee_eth": "0.00002",
        "sell_tx": "0f24f18fd5bc796d06c07defc4f02d45b2a958ff775d3a76b0ca2264a67f8042",
        "sell_block": 12138915, "sell_received_eth": "0.0014887125",
        "sell_fee_eth": "0.0000150375", "sell_gross_eth": "0.00150375",
        "net_eth_ex_gas": "-0.00047625",
        "pool_before_eth": "0", "pool_after_eth": "0.00047625",
    },
    "cross_side_loop_a2_retest": loop_test,
    "final_markets": final_markets,
    "wallet_balance_after_eth": str(w3.from_wei(bal_f, "ether")),
    "wallet_nonce_after": nonce_f,
    "results": results,
}
with open(OUT, "w") as f:
    json.dump(state, f, indent=1)
fails = [r for r in results if r["result"] == "FAIL"]
print(f"TOTAL: {len(results)-len(fails)}/{len(results)} passed", flush=True)
print(f"factory={FACTORY_ADDR}", flush=True)
print(f"wallet balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}", flush=True)
