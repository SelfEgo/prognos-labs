# Corrected live proof for the v4 Fed market.
# The deploy script was stopped on a stale-node position read: the buyYes
# 0.002 ETH DID succeed on-chain (deployer holds 3 Yes, pool 0.00198 ETH),
# but the node returned a stale (0,0) position, so sellYes(0) could not be
# estimated. This script finishes the proof with POLLED reads.
#  (a) Round trip: sell the 3 Yes shares from the 0.002 ETH buy.
#  (b) Cross-side loop (Attack A2 re-test): buy Yes 0.005, buy No 0.005,
#      sell all Yes, sell all No.
# Every position read POLLS until the node returns the expected value
# (publicnode serves stale reads); never trust a single read.
# Testnet only, chain 11155111. Never logs the key. Never resolves a market.
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python sepolia-deploy-v4-proof.py
import json, os, time
import solcx
from web3 import Web3
from eth_account import Account

BASE = os.path.dirname(os.path.abspath(__file__))
RPC = "https://ethereum-sepolia-rpc.publicnode.com"
OUT = f"{BASE}/sepolia-deploy-v4-proof.json"
FED_ADDR = "0x0b1D8Af50888028ad9f6c9A5cfF7EDd699b29aB6"
FACTORY_ADDR = "0x9F0975e862B57cC34Fb62aB9f7F58d368D6DE4Ef"
RESERVE_ETH = 0.005

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
    """Poll getPosition until the node returns the expected shares."""
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
market = w3.eth.contract(address=FED_ADDR, abi=abis["PrognosMarket"][0])

# Confirm the starting position (the 0.002 ETH buyYes from the deploy run).
pos0 = wait_position(market, 3, 0)
pool0 = w3.eth.get_balance(FED_ADDR)
log("Fed v4 starting position is 3 Yes / 0 No (deploy buy confirmed)", list(pos0) == [3, 0],
    f"pos={list(pos0)} pool={w3.from_wei(pool0, 'ether')} ETH")

# (a) Round trip: sell the 3 Yes shares.
#     The buy sent 0.002 ETH and the 1% fee (0.00002 ETH) went back to the
#     deployer as fee recipient, so the net outlay was 0.00198 ETH.
#     The sell pays gross = sum of the 3 share prices; the deployer gets
#     net + fee = gross. Round-trip net = gross - 0.00198 ETH.
wb0 = w3.eth.get_balance(deployer)
txh_rts, rcpt_rts = send(market.functions.sellYes(3), gas_floor=800_000)
pos1 = wait_position(market, 0, 0)
wa1 = w3.eth.get_balance(deployer)
pool1 = w3.eth.get_balance(FED_ADDR)
logs = market.events.Sell().process_receipt(rcpt_rts)
sell_ev = logs[0]["args"] if logs else None
gross_sell = sell_ev["received"] + sell_ev["fee"] if sell_ev else None
net_rt = (gross_sell - w3.to_wei(0.00198, "ether")) if gross_sell is not None else None
log("live round trip Fed 0.002 ETH loses money, pool grows, book back to 0/0",
    rcpt_rts.status == 1 and net_rt is not None and net_rt <= 0 and list(pos1) == [0, 0] and pool1 >= pool0,
    f"sold 3 Yes, gross={w3.from_wei(gross_sell, 'ether') if gross_sell is not None else '?'} ETH, "
    f"net {w3.from_wei(net_rt, 'ether') if net_rt is not None else '?'} ETH, "
    f"pool {w3.from_wei(pool0, 'ether')}->{w3.from_wei(pool1, 'ether')} ETH, tx={txh_rts} gas={rcpt_rts.gasUsed}")
round_trip = {
    "value_eth": 0.002, "shares_bought": 3, "shares_sold": 3,
    "sell_gross_eth": str(w3.from_wei(gross_sell, "ether")) if gross_sell is not None else None,
    "sell_received_eth": str(w3.from_wei(sell_ev["received"], "ether")) if sell_ev else None,
    "sell_fee_eth": str(w3.from_wei(sell_ev["fee"], "ether")) if sell_ev else None,
    "net_eth_ex_gas": str(w3.from_wei(net_rt, "ether")) if net_rt is not None else None,
    "pool_before_eth": str(w3.from_wei(pool0, "ether")),
    "pool_after_eth": str(w3.from_wei(pool1, "ether")),
    "tx_sell": txh_rts, "gas_sell": rcpt_rts.gasUsed,
}

# (b) Cross-side loop (Attack A2 re-test): buy Yes 0.005, buy No 0.005,
#     sell all Yes, sell all No. Must LOSE (only buy dust) and the pool
#     must not shrink. Book must return to 0/0.
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
    f"buyYes 9, buyNo 10, net {w3.from_wei(net_loop, 'ether')} ETH, "
    f"pool {w3.from_wei(poolL0, 'ether')}->{w3.from_wei(poolL1, 'ether')} ETH, "
    f"txs buyYes={txh_lb1} buyNo={txh_lb2} sellYes={txh_ls1} sellNo={txh_ls2}")
loop_test = {
    "buy_yes_eth": 0.005, "buy_no_eth": 0.005,
    "shares_bought_yes": 9, "shares_bought_no": 10,
    "shares_sold_yes": 9, "shares_sold_no": 10,
    "net_eth_ex_gas": str(w3.from_wei(net_loop, "ether")),
    "pool_before_eth": str(w3.from_wei(poolL0, "ether")),
    "pool_after_eth": str(w3.from_wei(poolL1, "ether")),
    "book_after": list(pos_end),
    "tx": {"buy_yes": txh_lb1, "buy_no": txh_lb2, "sell_yes": txh_ls1, "sell_no": txh_ls2},
    "gas": {"buy_yes": rcpt_lb1.gasUsed, "buy_no": rcpt_lb2.gasUsed,
            "sell_yes": rcpt_ls1.gasUsed, "sell_no": rcpt_ls2.gasUsed},
}

# Final state of all 6 v4 markets
factory = w3.eth.contract(address=FACTORY_ADDR, abi=abis["MarketFactory"][0])
markets_f = None
for _ in range(12):
    try:
        markets_f = factory.functions.getMarkets().call()
        if len(markets_f) == 6:
            break
    except Exception:
        pass
    time.sleep(5)
final_markets = []
for addr in (markets_f or []):
    m = w3.eth.contract(address=addr, abi=abis["PrognosMarket"][0])
    ys = call_with_retry(m.functions.yesShares())
    ns = call_with_retry(m.functions.noShares())
    pool = w3.eth.get_balance(addr)
    res = call_with_retry(m.functions.resolved())
    q = call_with_retry(m.functions.question())
    ex = call_with_retry(m.functions.expiry())
    final_markets.append({"question": q, "expiry": ex, "market_address": addr,
                          "yes_shares": ys, "no_shares": ns,
                          "pool_eth": str(w3.from_wei(pool, "ether")), "resolved": res})
    print(f"  FINAL {addr}: Yes {ys} / No {ns}, pool {w3.from_wei(pool, 'ether')} ETH, resolved {res}", flush=True)

bal_f = w3.eth.get_balance(deployer)
nonce_f = w3.eth.get_transaction_count(deployer, "pending")
log("wallet above 0.005 ETH reserve after proof", float(w3.from_wei(bal_f, "ether")) >= RESERVE_ETH,
    f"balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}")

state = {
    "status": "OK" if all(r["result"] == "PASS" for r in results) else "FAILURES",
    "network": "Sepolia", "chain_id": 11155111, "version": "v4 difference curve",
    "deployer": deployer,
    "factory_address": FACTORY_ADDR,
    "fed_market_address": FED_ADDR,
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
print(f"wallet balance={w3.from_wei(bal_f, 'ether')} ETH nonce={nonce_f}", flush=True)
