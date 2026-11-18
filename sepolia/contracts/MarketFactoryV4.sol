// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MarketFactory - creates PrognosMarket binary prediction markets.
/// @notice Sepolia testnet ONLY (chain id 11155111). Unaudited. Test use only.
import "./PrognosMarketV4.sol";

contract MarketFactory {
    address[] public markets;

    event MarketCreated(
        address indexed market,
        address indexed creator,
        address indexed resolver,
        string question,
        uint256 expiry
    );

    /// @notice Create a new binary market.
    /// @param question The market question, e.g. "Will the Federal Reserve cut
    ///        rates in December 2026?"
    /// @param expiry Unix timestamp after which the market can be resolved.
    ///        Must be in the future.
    /// @param resolver Address allowed to resolve the market (admin, test only).
    ///        Pass address(0) to make the creator the resolver.
    /// @return market The address of the newly created PrognosMarket.
    /// @dev The 1% trade fee goes to the creator (msg.sender) of each market.
    function createMarket(
        string calldata question,
        uint256 expiry,
        address resolver
    ) external returns (address market) {
        require(bytes(question).length > 0, "empty question");
        require(expiry > block.timestamp, "expiry in the past");

        address resolverAddress = resolver == address(0) ? msg.sender : resolver;

        PrognosMarket created = new PrognosMarket(question, expiry, resolverAddress, msg.sender);
        market = address(created);
        markets.push(market);

        emit MarketCreated(market, msg.sender, resolverAddress, question, expiry);
    }

    /// @notice Number of markets created by this factory.
    function marketCount() external view returns (uint256) {
        return markets.length;
    }

    /// @notice All market addresses created by this factory.
    function getMarkets() external view returns (address[] memory) {
        return markets;
    }
}
