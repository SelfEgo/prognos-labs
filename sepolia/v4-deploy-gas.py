# Measure v4 deploy gas locally (eth-tester): factory deploy + create the same
# 6 markets, so the Sepolia redeploy cost can be estimated at observed gas prices.
# Run: ~/workspace/prognos-labs/sepolia/.venv/bin/python v4-deploy-gas.py
import json
import os
import solcx
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider
from eth_tester import EthereumTester, PyEVMBackend

solcx.set_solc_version("0.8.24")
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")
MARKETS = [
    ("Will the Federal Reserve cut rates in December 2026?", 1795293827),
    ("Will Anthropic (ANT) begin trading on Nasdaq by 19 November 2026?", 1795150800),
    ("Will Liverpool beat Manchester United on 22 November 2026?", 1795388340),
    ("Will the People's Party (PP) win the most seats in Spain's general election on 29 November 2026?", 1796079540),
    ("Will GTA VI release on 19 November 2026 as scheduled?", 1795150800),
    ("Will GTA VI be nominated for Game of the Year at The Game Awards 2026?", 1794956340),
]

def main():
    compiled = {}
    for name, fname in [("MarketFactory", "MarketFactoryV4")]:
        c = solcx.compile_files([f"{BASE}/{fname}.sol"], output_values=["abi", "bin"],
                                solc_version="0.8.24", optimize=True, optimize_runs=200)
        key = next(k for k in c if k.endswith(f":{name}"))
        compiled[name] = (c[key]["abi"], c[key]["bin"])
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    deployer = w3.eth.accounts[0]
    factory = w3.eth.contract(abi=compiled["MarketFactory"][0], bytecode=compiled["MarketFactory"][1])
    tx = factory.constructor().transact({"from": deployer})
    rcpt = w3.eth.wait_for_transaction_receipt(tx)
    factory = w3.eth.contract(address=rcpt.contractAddress, abi=compiled["MarketFactory"][0])
    creates = []
    for q, exp in MARKETS:
        tx = factory.functions.createMarket(q, exp, deployer).transact({"from": deployer})
        r = w3.eth.wait_for_transaction_receipt(tx)
        creates.append(r.gasUsed)
    total_gas = rcpt.gasUsed + sum(creates)
    print(f"factory deploy gas: {rcpt.gasUsed}")
    print(f"market create gas each: {creates}")
    print(f"total gas (factory + 6 markets): {total_gas}")
    print("WARNING: local eth-tester UNDERCHARGES CREATE gas ~7x vs Sepolia.")
    print("Local numbers below are NOT the Sepolia cost and must not be used for funding.")
    print("Sepolia receipts for the byte-identical v3 code: factory deploy 12,043,183 gas,")
    print("create 9,490,445 gas; Sepolia estimator for v4: factory 12,473,467, create ~9,468,489.")
    print("Sepolia-verified v4 deploy at 0.5 gwei: factory ~0.0062 + 6 markets ~0.0284 = ~0.035 ETH.")
    print("See v4-deploy-gas.json for the verified figures. Local gas shown for reference only:")
    for gwei in [0.5, 1.0, 2.0]:
        cost = total_gas * gwei * 1e9 / 1e18
        print(f"LOCAL ONLY at {gwei} gwei: {cost:.6f} ETH (factory {rcpt.gasUsed * gwei * 1e9 / 1e18:.6f} + markets {sum(creates) * gwei * 1e9 / 1e18:.6f}) -- DO NOT USE FOR FUNDING")
    print("observed Sepolia v3 costs for comparison: factory ~0.006 ETH, each market ~0.0047 ETH, total ~0.034 ETH")
    json.dump({"factory_gas": rcpt.gasUsed, "create_gas": creates, "total_gas": total_gas},
              open(f"{BASE}/v4-deploy-gas.json", "w"), indent=1)

if __name__ == "__main__":
    main()
