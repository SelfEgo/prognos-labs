# Sepolia deploy of the v4 (difference-curve) Prognos contracts.
# Deploys a NEW factory, creates the SAME 6 markets that exist on v3
# (Fed, Anthropic IPO, Liverpool vs Man Utd, Spain PP, GTA VI release,
# GTA VI GOTY), then proves on the real network:
#  (a) a sell succeeds on a one-sided book,
#  (b) a small same-side round trip LOSES money (v2 arbitrage gone),
#  (c) a live cross-side loop (buy Yes, buy No, sell Yes, sell No) LOSES
#      money -- Attack A2 from the v3 black-hat test is GONE live.
# Testnet only, chain 11155111. Never logs the key. Never resolves a market.
# SAFETY: refuses to start unless the wallet holds at least REQUIRED_MIN_ETH
# (~0.045 ETH) and stops creating if the balance would drop below the
# 0.005 ETH reserve.
# Gas: publicnode lags and under-estimates (v3's first buyYes ran out of gas
# on a 207k estimate vs ~382k real cost), so every tx uses
# gas_limit = max(estimate * 2, floor) with a floor per tx type.
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python sepolia-deploy-v4.py
import json, os, time
import solcx
from web3 import Web3
from eth_account import Account

BASE = os.path.dirname(os.path.abspath(__file__))
RPC = "https://ethereum-sepolia-rpc.publicnode.com"
OUT = f"{BASE}/sepolia-deploy-v4-results.json"
REQUIRED_MIN_ETH = 0.045  # all-in ~0.041 ETH (factory ~0.0062 + 6 creates ~0.0285 + trades); wallet has ~0.0626 ETH
RESERVE_ETH = 0.005
EST_COST_PER_CREATE_ETH = 0.006  # conservative guard (observed ~0.0047)

# Same 6 markets as v3, same questions and expiries.
MARKETS = [
    ("Fed", "Will the Federal Reserve cut rates in December 2026?", 1795293827),  # 21 Nov 2026 ~20:43 WAT
    ("Anthropic IPO", "Will Anthropic (ANT) begin trading on Nasdaq by 19 November 2026?", 1795150800),  # Fri 20 Nov 2026 06:00 WAT
    ("Liverpool vs Man Utd", "Will Liverpool beat Manchester United on 22 November 2026?", 1795388340),  # Sun 22 Nov 2026 23:59 WAT
    ("Spain PP", "Will the People's Party (PP) win the most seats in Spain's general election on 29 November 2026?", 1796079540),  # Mon 30 Nov 2026 23:59 WAT
    ("GTA VI release", "Will GTA VI release on 19 November 2026 as scheduled?", 1795150800),  # Fri 20 Nov 2026 06:00 WAT
    ("GTA VI GOTY", "Will GTA VI be nominated for Game of the Year at The Game Awards 2026?", 1794956340),  # Tue 17 Nov 2026 23:59 WAT
]

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
bal = w3.eth.get_balance(deployer)
nonce = w3.eth.get_transaction_count(deployer, "pending")
print(f"deployer={deployer} balance={w3.from_wei(bal, 'ether')} ETH nonce={nonce}", flush=True)
if float(w3.from_wei(bal, "ether")) < REQUIRED_MIN_ETH:
    print(f"BLOCKED: wallet holds {w3.from_wei(bal, 'ether')} ETH, need at least {REQUIRED_MIN_ETH} ETH "
          f"(~0.041 ETH estimated all-in). Send at least {REQUIRED_MIN_ETH - float(w3.from_wei(bal, 'ether')):.6f} more Sepolia ETH to {deployer} "
          f"and re-run this script. No transaction was sent.", flush=True)
    with open(OUT, "w") as f:
        json.dump({"status": "BLOCKED_ON_FUNDS", "network": "Sepolia", "chain_id": 11155111,
                   "version": "v4 difference curve", "deployer": deployer,
                   "wallet_balance_eth": str(w3.from_wei(bal, "ether")),
                   "wallet_nonce": nonce,
                   "required_min_eth": REQUIRED_MIN_ETH,
                   "shortfall_eth": REQUIRED_MIN_ETH - float(w3.from_wei(bal, "ether")),
                   "results": results}, f, indent=1)
    raise SystemExit(0)

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
        # Lagging node: poll for the receipt.
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
print("compiled v4 contracts", flush=True)

# 1. Deploy factory v4
factory_impl = w3.eth.contract(abi=abis["MarketFactory"][0], bytecode=abis["MarketFactory"][1])
txh0, rcpt0 = send(factory_impl.constructor(), gas_floor=15_000_000)
factory_addr = rcpt0.contractAddress
log("deploy factory v4", rcpt0.status == 1 and factory_addr,
    f"addr={factory_addr} gas={rcpt0.gasUsed} tx={txh0} https://sepolia.etherscan.io/tx/{txh0}")
if rcpt0.status != 1:
    print("STOP: factory deploy failed on-chain.", flush=True)
    raise SystemExit(1)
factory = w3.eth.contract(address=factory_addr, abi=abis["MarketFactory"][0])
count = call_with_retry(factory.functions.marketCount())
log("factory live, marketCount 0", count == 0, f"addr={factory_addr}")

# 2. Create the same 6 markets as v3
created = []
skipped = []
for i, (key, question, expiry) in enumerate(MARKETS, 1):
    bal_now = float(w3.from_wei(w3.eth.get_balance(deployer), "ether"))
    if bal_now - EST_COST_PER_CREATE_ETH < RESERVE_ETH:
        print(f"STOP market {i} ({key}): balance {bal_now:.6f} ETH would drop below {RESERVE_ETH} ETH reserve. Skipping remaining.", flush=True)
        skipped.append({"index": i, "key": key, "question": question, "expiry": expiry,
                        "reason": f"reserve guard: balance {bal_now:.6f} ETH"})
        for key2, q2, e2 in MARKETS[i:]:
            skipped.append({"index": MARKETS.index((key2, q2, e2)) + 1, "key": key2, "question": q2, "expiry": e2,
                            "reason": "skipped after reserve guard stopped earlier market"})
        break
    if expiry <= int(time.time()) + 60:
        print(f"SKIP market {i} ({key}): expiry {expiry} not in future", flush=True)
        skipped.append({"index": i, "key": key, "question": question, "expiry": expiry, "reason": "expiry not in future"})
        continue
    print(f"Creating market {i} ({key}): {question} expiry={expiry}", flush=True)
    txh, rcpt = send(factory.functions.createMarket(question, expiry, deployer), gas_floor=12_000_000)
    print(f"  tx={txh} status={rcpt.status} gasUsed={rcpt.gasUsed}", flush=True)
    if rcpt.status != 1:
        skipped.append({"index": i, "key": key, "question": question, "expiry": expiry,
                        "reason": f"on-chain tx failed status={rcpt.status} tx={txh}"})
        continue
    logs = factory.events.MarketCreated().process_receipt(rcpt)
    market_addr = logs[0]["args"]["market"] if logs else None
    count = call_with_retry(factory.functions.marketCount())
    markets = call_with_retry(factory.functions.getMarkets())
    market = w3.eth.contract(address=market_addr, abi=abis["PrognosMarket"][0])
    q = call_with_retry(market.functions.question())
    ex = call_with_retry(market.functions.expiry())
    res = call_with_retry(market.functions.resolved())
    ok = (q == question and ex == expiry and res is False and market_addr in markets)
    print(f"  VERIFY market={market_addr} count={count} question_match={q == question} expiry_match={ex == expiry} resolved={res} in_list={market_addr in markets} -> {'OK' if ok else 'MISMATCH'}", flush=True)
    log(f"create market {i} ({key}) v4", ok,
        f"addr={market_addr} expiry={expiry} gas={rcpt.gasUsed} tx={txh} https://sepolia.etherscan.io/tx/{txh}")
    created.append({"index": i, "key": key, "question": question, "expiry": expiry,
                    "market_address": market_addr, "tx_hash": txh,
                    "gas_used": rcpt.gasUsed, "verified": ok,
                    "factory_market_count_after": count})

count_f = call_with_retry(factory.functions.marketCount())
markets_f = call_with_retry(factory.functions.getMarkets())
log("factory marketCount 6", count_f == 6, f"markets={markets_f}")
if count_f != 6:
    print(f"STOP: factory marketCount is {count_f}, expected 6. Created {len(created)}, skipped {len(skipped)}. Not proceeding to the live proof.", flush=True)
    with open(OUT, "w") as f:
        json.dump({"status": "PARTIAL", "network": "Sepolia", "chain_id": 11155111,
                   "version": "v4 difference curve", "deployer": deployer,
                   "factory_address": factory_addr, "deploy_factory_tx": txh0,
                   "created": created, "skipped": skipped,
                   "factory_market_count": count_f, "markets": markets_f,
                   "wallet_balance_eth": str(w3.from_wei(w3.eth.get_balance(deployer), "ether")),
                   "wallet_nonce": w3.eth.get_transaction_count(deployer, "pending"),
                   "results": results}, f, indent=1)
    raise SystemExit(1)

markets_by_key = {c["key"]: c["market_address"] for c in created}
fed_addr = markets_by_key["Fed"]
market = w3.eth.contract(address=fed_addr, abi=abis["PrognosMarket"][0])
p0 = call_with_retry(market.functions.getPrices())
log("Fed v4 starting price 50/50", p0[0] == p0[1] == 500_000_000_000_000, f"yes={p0[0]} no={p0[1]} wei")
log("Fed v4 not resolved", call_with_retry(market.functions.resolved()) is False, "")

# 3a. Small same-side round trip on the Fed market: buy Yes 0.002, sell all back.
#     Deployer is the fee recipient, so fees come back; the round trip only
#     loses the buy dust. Net is measured from the wallet balance plus gas.
pool0 = w3.eth.get_balance(fed_addr)
wb0 = w3.eth.get_balance(deployer)
txh_rtb, rcpt_rtb = send(market.functions.buyYes(), value=w3.to_wei(0.002, "ether"), gas_floor=800_000)
pos = call_with_retry(market.functions.getPosition(deployer))
sh_rt = pos[0]
txh_rts, rcpt_rts = send(market.functions.sellYes(sh_rt), gas_floor=800_000)
wa2 = w3.eth.get_balance(deployer)
pos2 = call_with_retry(market.functions.getPosition(deployer))
pool1 = w3.eth.get_balance(fed_addr)
gas_rt = rcpt_rtb.gasUsed * rcpt_rtb.effectiveGasPrice + rcpt_rts.gasUsed * rcpt_rts.effectiveGasPrice
net_rt = (wa2 - wb0) + gas_rt
log("live round trip Fed 0.002 ETH loses money, pool grows, book back to 0/0",
    rcpt_rtb.status == 1 and rcpt_rts.status == 1 and net_rt <= 0 and pos2[0] == 0 and pos2[1] == 0 and pool1 >= pool0,
    f"bought {sh_rt} Yes, net {w3.from_wei(net_rt, 'ether')} ETH, pool {w3.from_wei(pool0, 'ether')}->{w3.from_wei(pool1, 'ether')} ETH, tx buy={txh_rtb} sell={txh_rts}")
round_trip = {
    "value_eth": 0.002, "shares_bought": sh_rt, "shares_sold": sh_rt,
    "net_eth_ex_gas": str(w3.from_wei(net_rt, "ether")),
    "pool_before_eth": str(w3.from_wei(pool0, "ether")),
    "pool_after_eth": str(w3.from_wei(pool1, "ether")),
    "tx_buy": txh_rtb, "tx_sell": txh_rts,
    "gas_buy": rcpt_rtb.gasUsed, "gas_sell": rcpt_rts.gasUsed,
}

# 3b. Live cross-side loop (Attack A2 re-test) on the Fed market:
#     buy Yes 0.005, buy No 0.005, sell all Yes, sell all No.
#     On v3 this loop profited and drained the pool. On v4 every closed loop
#     nets zero before fees, so it must LOSE (only the buy dust) and the
#     pool must not shrink. Book must return to 0/0.
poolL0 = w3.eth.get_balance(fed_addr)
wbL0 = w3.eth.get_balance(deployer)
txh_lb1, rcpt_lb1 = send(market.functions.buyYes(), value=w3.to_wei(0.005, "ether"), gas_floor=800_000)
pos = call_with_retry(market.functions.getPosition(deployer))
sh_loop_yes = pos[0]
txh_lb2, rcpt_lb2 = send(market.functions.buyNo(), value=w3.to_wei(0.005, "ether"), gas_floor=800_000)
pos = call_with_retry(market.functions.getPosition(deployer))
sh_loop_no = pos[1]
log("loop buys", rcpt_lb1.status == 1 and rcpt_lb2.status == 1,
    f"buyYes {sh_loop_yes} shares (expect 9), buyNo {sh_loop_no} shares (expect 10), pos={list(pos)}")
txh_ls1, rcpt_ls1 = send(market.functions.sellYes(sh_loop_yes), gas_floor=800_000)
pos = call_with_retry(market.functions.getPosition(deployer))
txh_ls2, rcpt_ls2 = send(market.functions.sellNo(sh_loop_no), gas_floor=800_000)
pos_end = call_with_retry(market.functions.getPosition(deployer))
waL = w3.eth.get_balance(deployer)
poolL1 = w3.eth.get_balance(fed_addr)
gas_loop = sum(r.gasUsed * r.effectiveGasPrice for r in [rcpt_lb1, rcpt_lb2, rcpt_ls1, rcpt_ls2])
net_loop = (waL - wbL0) + gas_loop
loop_ok = (all(r.status == 1 for r in [rcpt_lb1, rcpt_lb2, rcpt_ls1, rcpt_ls2])
           and net_loop <= 0 and pos_end[0] == 0 and pos_end[1] == 0 and poolL1 >= poolL0)
log("LIVE cross-side loop Fed (A2 re-test) LOSES money, pool grows, book back to 0/0", loop_ok,
    f"buyYes {sh_loop_yes}, buyNo {sh_loop_no}, net {w3.from_wei(net_loop, 'ether')} ETH, "
    f"pool {w3.from_wei(poolL0, 'ether')}->{w3.from_wei(poolL1, 'ether')} ETH, "
    f"txs buyYes={txh_lb1} buyNo={txh_lb2} sellYes={txh_ls1} sellNo={txh_ls2}")
loop_test = {
    "buy_yes_eth": 0.005, "buy_no_eth": 0.005,
    "shares_bought_yes": sh_loop_yes, "shares_bought_no": sh_loop_no,
    "shares_sold_yes": sh_loop_yes, "shares_sold_no": sh_loop_no,
    "net_eth_ex_gas": str(w3.from_wei(net_loop, "ether")),
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
    q = call_with_retry(m.functions.question())
    ex = call_with_retry(m.functions.expiry())
    final_markets.append({"key": c["key"], "question": q, "expiry": ex,
                          "market_address": c["market_address"],
                          "yes_shares": ys, "no_shares": ns,
                          "pool_eth": str(w3.from_wei(pool, "ether")),
                          "resolved": res})
    print(f"  FINAL {c['key']}: Yes {ys} / No {ns}, pool {w3.from_wei(pool, 'ether')} ETH, resolved {res}", flush=True)

bal_f = w3.eth.get_balance(deployer)
nonce_f = w3.eth.get_transaction_count(deployer, "pending")
log("wallet above 0.005 ETH reserve after deploy", float(w3.from_wei(bal_f, "ether")) >= RESERVE_ETH,
    f"balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}")

state = {
    "status": "OK" if all(r["result"] == "PASS" for r in results) else "FAILURES",
    "network": "Sepolia", "chain_id": 11155111, "version": "v4 difference curve",
    "deployer": deployer,
    "factory_address": factory_addr,
    "deploy_factory_tx": txh0,
    "deploy_factory_gas": rcpt0.gasUsed,
    "factory_market_count": count_f,
    "markets": markets_f,
    "created": created,
    "skipped": skipped,
    "fed_market_address": fed_addr,
    "round_trip": round_trip,
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
print(f"factory={factory_addr}", flush=True)
print(f"wallet balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}", flush=True)
