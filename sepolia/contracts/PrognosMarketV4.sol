// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title PrognosMarket - a simple binary (Yes/No) prediction market.
/// @notice Sepolia testnet ONLY (chain id 11155111). Unaudited. Test use only.
/// @dev No external imports, no oracle. One market = one yes/no question.
///      Collateral is ETH. Shares are whole units (no decimals).
///      SETTLEMENT (v2, parimutuel): winners split the pool pro-rata.
///      Redeem payout = contract balance x (caller's winning shares /
///      total winning shares). There is no fixed payout per share, so the
///      pool can never be short: the total payout is always exactly the
///      pool (up to a few wei of integer-division dust). The 1% fee on buys
///      and sells just reduces the pool slightly, which is fine.
///      This replaces the v1 fixed payout (1 share = 0.001 ETH), which was
///      insolvent on an imbalanced book. See SEPOLIA_TEST_REPORT.md §7c.
///      PRICING (v4, difference curve - a cost function, not a surface):
///      the marginal price depends ONLY on the difference d = yesShares
///      minus noShares, never on the two counts separately:
///      pYes = NOTIONAL x (400 + clamp(d, -360, +360)) / 800,
///      pNo  = NOTIONAL - pYes. At d = 0 both are 0.0005 ETH (50/50); the
///      price moves 0.001/800 ETH per share of difference and caps at
///      0.00095 / 0.00005 ETH (|d| >= 360).
///      WHY THIS FIXES v3 (BLACKHAT_V2_REPORT.md, Attack A2): v3 priced a
///      trade as the integral of a marginal price that depended on
///      (yesShares, noShares) separately. That price field has no cost
///      function (d(pYes)/d(noShares) != d(pNo)/d(yesShares)), so the cost
///      of reaching a book depended on the PATH taken to it. An attacker
///      could walk a closed loop in (yesShares, noShares) space - buy Yes,
///      buy No, sell Yes, sell No, ending with zero shares - and be paid
///      more than they put in: on a seeded book the loop profited up to
///      +0.062 ETH after gas on a 1.0 ETH loop and drained 84% of the pool,
///      and it paid the same amount again on every repeat. The v3
///      integrated pricing only made SAME-side round trips exact inverses.
///      In v4 the price of a step depends only on the level d it moves
///      between, so every buy step is exactly undone by the matching sell
///      step at the same level: buy Yes at d pays pYes(d), selling that
///      share back from d+1 pays pYes(d); buy No at d pays pNo(d),
///      selling it back from d-1 pays pNo(d). ANY sequence of trades that
///      returns the book to its starting state therefore nets exactly zero
///      before fees and loses the 1% fees - no loop, in either direction,
///      on any book, can profit. The pool is exactly unchanged over a
///      closed loop. The per-share loop is kept (same gas profile as v3);
///      only the pricer changed. A trade's cost is still the integral of
///      the marginal price over the shares traded (see _buy / _sell).
///      Price curve: implied probability from the share imbalance, used to
///      quote the MARGINAL price of the next single share. See
///      priceYes()/priceNo() and README.md. 1% fee on buys and sells.
///      Gas note: the per-share loop makes a trade cost O(shares) gas
///      (a few hundred gas per share after the first storage write), so
///      very large trades are expensive. That is accepted for this test
///      prototype; a closed-form cost function may replace the loop later
///      (the v4 price has one: the cost of a trade is a closed form in d,
///      but the loop is kept so buy and sell stay exact inverses at the
///      integer level).
contract PrognosMarket {
    // ------------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------------

    /// @notice Price-curve scale, in wei. The Yes and No prices always sum
    ///         to this (0.001 ETH). It is NOT the redeem payout anymore:
    ///         redeem is parimutuel (see redeem()), so a winning share is
    ///         worth pool / total winning shares. Kept at 0.001 ETH so the
    ///         quoted prices still read as complementary probabilities.
    uint256 public constant SHARE_NOTIONAL = 0.001 ether;

    /// @notice Fee on buys and sells, in basis points (100 = 1%).
    uint256 public constant FEE_BPS = 100;

    /// @notice Maximum |yesShares - noShares| before the price caps.
    ///         At |d| >= 360 the Yes price sticks at 0.00095 ETH and the
    ///         No price at 0.00005 ETH (or the mirror image).
    int256 public constant DIFF_CAP = 360;

    /// @notice Denominator of the v4 price formula. pYes = NOTIONAL x
    ///         (400 + clamped difference) / 800, so at d = 0 the price is
    ///         exactly NOTIONAL / 2 and each share of difference moves it
    ///         by NOTIONAL / 800.
    uint256 public constant PRICE_DENOM = 800;

    uint256 private constant BPS_DENOMINATOR = 10_000;

    // ------------------------------------------------------------------------
    // Types / state
    // ------------------------------------------------------------------------

    enum Outcome {
        Unresolved,
        Yes,
        No,
        Void
    }

    string public question;
    uint256 public expiry;
    address public resolver;
    address public feeRecipient;
    address public factory;

    Outcome public outcome;
    bool public resolved;

    uint256 public yesShares;
    uint256 public noShares;

    mapping(address => uint256) public yesBalance;
    mapping(address => uint256) public noBalance;

    // Minimal reentrancy guard, written inline (no external imports).
    uint256 private _locked;

    // ------------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------------

    event Buy(address indexed buyer, bool indexed isYes, uint256 shares, uint256 paid, uint256 fee);
    event Sell(address indexed seller, bool indexed isYes, uint256 shares, uint256 received, uint256 fee);
    event Resolve(Outcome indexed outcome);
    event Redeem(address indexed redeemer, bool indexed isYes, uint256 shares, uint256 payout);

    // ------------------------------------------------------------------------
    // Modifiers
    // ------------------------------------------------------------------------

    modifier nonReentrant() {
        require(_locked == 0, "reentrant call");
        _locked = 1;
        _;
        _locked = 0;
    }

    modifier onlyResolver() {
        require(msg.sender == resolver, "not resolver");
        _;
    }

    // ------------------------------------------------------------------------
    // Constructor
    // ------------------------------------------------------------------------

    constructor(
        string memory _question,
        uint256 _expiry,
        address _resolver,
        address _feeRecipient
    ) {
        require(bytes(_question).length > 0, "empty question");
        require(_expiry > block.timestamp, "expiry in the past");
        require(_resolver != address(0), "resolver is zero");
        require(_feeRecipient != address(0), "fee recipient is zero");

        question = _question;
        expiry = _expiry;
        resolver = _resolver;
        feeRecipient = _feeRecipient;
        factory = msg.sender; // expected to be the MarketFactory
        outcome = Outcome.Unresolved;
    }

    // ------------------------------------------------------------------------
    // Pricing
    // ------------------------------------------------------------------------

    /// @notice Current (marginal) price of one Yes share, in wei: the price
    ///         of the NEXT single Yes share at the current book.
    /// @dev v4: pYes = NOTIONAL * (400 + clamp(yesShares - noShares,
    ///      -360, +360)) / 800. Starts at 0.0005 ETH. Buying Yes raises
    ///      pYes; buying No lowers it. A multi-share trade is NOT priced
    ///      at this single price: it is priced as the integral of this
    ///      curve over the shares traded (see _buy / _sell). This function
    ///      only quotes the marginal price.
    function priceYes() public view returns (uint256) {
        return _priceYesAt(_diff(yesShares, noShares));
    }

    /// @notice Current (marginal) price of one No share, in wei. pYes + pNo
    ///         = NOTIONAL exactly.
    function priceNo() public view returns (uint256) {
        return _priceNoAt(_diff(yesShares, noShares));
    }

    /// @dev The share difference d = yesShares - noShares, as a signed int.
    function _diff(uint256 y, uint256 n) internal pure returns (int256) {
        return int256(y) - int256(n);
    }

    /// @dev Marginal Yes price at a given difference d. The price depends
    ///         ONLY on d: that is the whole of the v4 fix (see the header).
    ///         The clamp keeps the price inside [0.00005, 0.00095] ETH.
    function _priceYesAt(int256 d) internal pure returns (uint256) {
        int256 clamped = d;
        if (clamped > DIFF_CAP) clamped = DIFF_CAP;
        if (clamped < -DIFF_CAP) clamped = -DIFF_CAP;
        return (SHARE_NOTIONAL * uint256(400 + clamped)) / PRICE_DENOM;
    }

    /// @dev Marginal No price at a given difference d. Exactly
    ///         NOTIONAL - pYes(d), so the two prices always sum to NOTIONAL.
    function _priceNoAt(int256 d) internal pure returns (uint256) {
        return SHARE_NOTIONAL - _priceYesAt(d);
    }

    function getPrices() external view returns (uint256 yesPrice, uint256 noPrice) {
        return (priceYes(), priceNo());
    }

    function getPosition(address user) external view returns (uint256 yes, uint256 no) {
        return (yesBalance[user], noBalance[user]);
    }

    /// @notice Estimated payout per winning share, in wei, if the market
    ///         resolved that way right now: the pool split by the winning
    ///         side's shares. Returns 0 for a side with no shares.
    /// @dev This is a view helper for the site. Redeem computes the same
    ///      value at the moment it is called.
    function getPayoutPerShare() external view returns (uint256 yesPayout, uint256 noPayout) {
        uint256 pool = address(this).balance;
        yesPayout = yesShares == 0 ? 0 : pool / yesShares;
        noPayout = noShares == 0 ? 0 : pool / noShares;
    }

    // ------------------------------------------------------------------------
    // Trading
    // ------------------------------------------------------------------------

    /// @notice Buy Yes shares with ETH. Priced as the integral of the
    ///         marginal price curve over the shares bought (v4): each share
    ///         costs the marginal price at the book when it is added, so a
    ///         large buy pays the rising average, not the pre-trade price
    ///         for all shares. See _buy().
    function buyYes() external payable nonReentrant {
        _buy(true);
    }

    /// @notice Buy No shares with ETH. Same pricing as buyYes().
    function buyNo() external payable nonReentrant {
        _buy(false);
    }

    function _buy(bool isYes) internal {
        require(!resolved, "market resolved");
        require(block.timestamp < expiry, "market expired");
        require(msg.value > 0, "no ETH sent");

        uint256 fee = (msg.value * FEE_BPS) / BPS_DENOMINATOR;
        uint256 remaining = msg.value - fee;

        // INTEGRATED PRICING (v3, kept in v4). Buy share by share. At each
        // step the marginal price is computed at the current book; if the
        // remaining value covers it, one share is bought, that price is
        // deducted, and the book moves by one share. The loop stops when
        // the value cannot cover the next share. The total cost is
        // therefore the exact integral of the price curve over the shares
        // bought. v4 CHANGE: the marginal price depends ONLY on the
        // difference d = yesShares - noShares (see _priceYesAt), so the
        // sell is the exact inverse of the buy not just for same-side
        // round trips but for ANY closed loop in the book (see the header).
        // The leftover dust (less than the next share's price) stays in
        // the contract, as in v2.
        uint256 y = yesShares;
        uint256 n = noShares;
        uint256 shares = 0;
        while (true) {
            uint256 price = isYes ? _priceYesAt(_diff(y, n)) : _priceNoAt(_diff(y, n));
            if (remaining < price) break;
            remaining -= price;
            shares += 1;
            if (isYes) {
                y += 1;
            } else {
                n += 1;
            }
        }
        require(shares > 0, "amount too small for 1 share");

        // Effects before the interaction (fee transfer) below.
        if (isYes) {
            yesShares = y;
            yesBalance[msg.sender] += shares;
        } else {
            noShares = n;
            noBalance[msg.sender] += shares;
        }

        if (fee > 0) {
            (bool feeOk, ) = feeRecipient.call{value: fee}("");
            require(feeOk, "fee transfer failed");
        }

        emit Buy(msg.sender, isYes, shares, msg.value, fee);
    }

    /// @notice Sell Yes shares back at the integral of the marginal price
    ///         curve over the shares sold (v4), minus the 1% fee. The sell
    ///         is the exact inverse of the buy before fees. See _sell().
    function sellYes(uint256 shares) external nonReentrant {
        _sell(true, shares);
    }

    /// @notice Sell No shares back at the integral of the marginal price
    ///         curve over the shares sold (v4), minus the 1% fee.
    function sellNo(uint256 shares) external nonReentrant {
        _sell(false, shares);
    }

    function _sell(bool isYes, uint256 shares) internal {
        require(!resolved, "market resolved");
        require(shares > 0, "no shares");
        if (isYes) {
            require(yesBalance[msg.sender] >= shares, "insufficient Yes shares");
        } else {
            require(noBalance[msg.sender] >= shares, "insufficient No shares");
        }

        // INTEGRATED PRICING (v3, kept in v4). The payout is the exact
        // integral of the marginal price curve as the shares are removed,
        // one by one. Removing a share first and then pricing at the new
        // (lower) book means each share sold is paid at exactly the price
        // it was bought at. v4: because the price depends only on the
        // difference d (see the header), this holds for ANY closed loop,
        // not just same-side round trips: a buy step at level d is exactly
        // undone by the matching sell step at the same level, so a closed
        // loop nets exactly zero before fees.
        uint256 y = yesShares;
        uint256 n = noShares;
        uint256 gross = 0;
        if (isYes) {
            for (uint256 i = 0; i < shares; i++) {
                y -= 1;
                gross += _priceYesAt(_diff(y, n));
            }
        } else {
            for (uint256 i = 0; i < shares; i++) {
                n -= 1;
                gross += _priceNoAt(_diff(y, n));
            }
        }

        uint256 fee = (gross * FEE_BPS) / BPS_DENOMINATOR;
        uint256 net = gross - fee;
        // v3 fix: the total outflow is fee + net = gross, so the check
        // must be against gross, not net. v2 checked net; a sell in the
        // [net, gross) window passed the check and reverted at the payout
        // with "payout failed" instead of the clean "insufficient contract
        // balance". (BLACKHAT_V2_REPORT.md, Attack B.)
        require(address(this).balance >= gross, "insufficient contract balance");

        // Parimutuel settlement (v2): a sell just takes money out of the
        // pool and the remaining winners split what is left, so a sell can
        // never break redemption.

        // Effects: burn the shares first, then pay out.
        if (isYes) {
            yesBalance[msg.sender] -= shares;
            yesShares = y;
        } else {
            noBalance[msg.sender] -= shares;
            noShares = n;
        }

        if (fee > 0) {
            (bool feeOk, ) = feeRecipient.call{value: fee}("");
            require(feeOk, "fee transfer failed");
        }
        (bool ok, ) = msg.sender.call{value: net}("");
        require(ok, "payout failed");

        emit Sell(msg.sender, isYes, shares, net, fee);
    }

    // ------------------------------------------------------------------------
    // Resolution / redemption
    // ------------------------------------------------------------------------

    /// @notice Admin-only resolution, for TESTING ONLY. Callable once, and only
    ///         after expiry. There is no oracle: whoever holds `resolver` decides
    ///         the outcome. Do not use with real value.
    /// @param _outcome 1 = Yes, 2 = No, 3 = Void. 0 (Unresolved) is rejected.
    function resolve(uint8 _outcome) external onlyResolver {
        require(!resolved, "already resolved");
        require(block.timestamp >= expiry, "not yet expired");
        require(_outcome >= 1 && _outcome <= 3, "invalid outcome");

        resolved = true;
        outcome = Outcome(_outcome);

        emit Resolve(outcome);
    }

    /// @notice Redeem winning shares, parimutuel: the winners split the pool.
    ///         Yes wins -> each Yes share is worth pool / yesShares.
    ///         No wins -> each No share is worth pool / noShares.
    ///         Void -> every share (Yes or No) is worth pool / (yesShares +
    ///         noShares); redeem one side per call. Losing shares pay nothing.
    ///         The total payout can never exceed the pool, so redemption is
    ///         always fully solvent. The caller's balance is zeroed and the
    ///         side's total shares are reduced before the payout is sent,
    ///         so later redeemers keep the same per-share value. The balance
    ///         is zeroed before payout, so a second redeem reverts with
    ///         "nothing to redeem".
    function redeem() external nonReentrant {
        require(resolved, "not resolved");

        uint256 shares;
        bool isYes;
        uint256 totalShares;
        if (outcome == Outcome.Yes) {
            shares = yesBalance[msg.sender];
            isYes = true;
            totalShares = yesShares;
        } else if (outcome == Outcome.No) {
            shares = noBalance[msg.sender];
            isYes = false;
            totalShares = noShares;
        } else {
            // Void: redeem whichever side the caller holds. Prefer Yes if both
            // are held; the other side can be redeemed with a second call.
            if (yesBalance[msg.sender] > 0) {
                shares = yesBalance[msg.sender];
                isYes = true;
            } else {
                shares = noBalance[msg.sender];
                isYes = false;
            }
            totalShares = yesShares + noShares;
        }
        require(shares > 0, "nothing to redeem");
        require(totalShares > 0, "no shares outstanding");

        // Parimutuel payout: the pool split by the winning side's shares.
        // Integer division rounds down, so a few wei of dust can remain.
        uint256 payout = (address(this).balance * shares) / totalShares;

        // Effects before interaction.
        if (isYes) {
            yesBalance[msg.sender] = 0;
            yesShares -= shares;
        } else {
            noBalance[msg.sender] = 0;
            noShares -= shares;
        }

        (bool ok, ) = msg.sender.call{value: payout}("");
        require(ok, "payout failed");

        emit Redeem(msg.sender, isYes, shares, payout);
    }

    // ------------------------------------------------------------------------
    // Fallbacks: reject stray ETH so it cannot silently break accounting.
    // ------------------------------------------------------------------------

    receive() external payable {
        revert("send ETH via buyYes/buyNo");
    }

    fallback() external payable {
        revert("unknown call");
    }
}
